"""
Миграция: Добавление поля reminder_interval_minutes в таблицу habits.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import async_session


async def migrate():
    """Добавление поля reminder_interval_minutes"""
    async with async_session() as session:
        # Проверяем, есть ли уже поле
        result = await session.execute(text("PRAGMA table_info(habits)"))
        columns = [row[1] for row in result.fetchall()]

        if "reminder_interval_minutes" not in columns:
            await session.execute(text("""
                ALTER TABLE habits ADD COLUMN reminder_interval_minutes INTEGER
            """))
            await session.commit()
            print("✅ Поле reminder_interval_minutes добавлено")
        else:
            print("ℹ️ Поле reminder_interval_minutes уже существует")


if __name__ == "__main__":
    asyncio.run(migrate())
