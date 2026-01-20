"""
Отправка сообщения всем пользователям о перезапуске бота
"""
import asyncio
import sqlite3
import os
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "bot_database.db"

async def broadcast_restart():
    bot = Bot(token=BOT_TOKEN)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT telegram_id, username, first_name FROM users")
    users = c.fetchall()
    conn.close()

    message = (
        "Привет! Это Джарвис 👋\n\n"
        "У меня появилась новая функция — **бронирование встреч**.\n\n"
        "Теперь ты можешь создать ссылку для записи, как в Calendly, "
        "и делиться ей с клиентами или коллегами.\n\n"
        "Нажми /start — появится новая кнопка 📅"
    )

    sent = 0
    failed = 0

    for telegram_id, username, first_name in users:
        try:
            await bot.send_message(telegram_id, message, parse_mode="Markdown")
            name = username or first_name or telegram_id
            print(f"✅ Отправлено: {name}")
            sent += 1
        except Exception as e:
            print(f"❌ Ошибка {telegram_id}: {e}")
            failed += 1

        await asyncio.sleep(0.1)  # Задержка чтобы не словить rate limit

    await bot.session.close()
    print(f"\n📊 Итого: отправлено {sent}, ошибок {failed}")

if __name__ == "__main__":
    asyncio.run(broadcast_restart())
