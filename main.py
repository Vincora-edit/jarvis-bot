"""
Точка входа приложения.
AI-ассистент с календарём, задачами и памятью.
"""
import asyncio
import logging
import sys
import os
import fcntl

from config import config

# Путь к PID-файлу
PID_FILE = "/tmp/jarvis_bot.pid"

# OAuth сервер runner (для cleanup)
_oauth_runner = None
# Booking сервер
_booking_server = None


def check_already_running():
    """Проверка, что бот уже не запущен"""
    try:
        # Пробуем получить эксклюзивную блокировку файла
        pid_file = open(PID_FILE, 'w')
        fcntl.flock(pid_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Записываем свой PID
        pid_file.write(str(os.getpid()))
        pid_file.flush()
        # Не закрываем файл — держим блокировку до конца работы
        return pid_file  # Возвращаем, чтобы не собрался garbage collector
    except (IOError, OSError):
        print("❌ Бот уже запущен! Завершаю дубль.")
        sys.exit(1)


from create_bot import dp, bot
from database import init_db, async_session
from scheduler import setup_scheduler
from handlers import user
from handlers import tunnel

logger = logging.getLogger(__name__)


async def run_booking_server():
    """Запустить FastAPI сервер для бронирования"""
    import uvicorn
    from booking.api import app as booking_app

    config_uvicorn = uvicorn.Config(
        booking_app,
        host="0.0.0.0",
        port=8082,
        log_level="warning",  # Меньше логов
    )
    server = uvicorn.Server(config_uvicorn)
    await server.serve()


async def on_startup():
    """Действия при запуске бота"""
    global _oauth_runner, _booking_server

    # Проверяем конфигурацию
    if not config.validate():
        raise ValueError("Ошибка конфигурации. Проверьте .env файл.")

    # ВАЖНО: Принудительно отключаем другие инстансы бота
    # Удаляем webhook и пробуем "вытеснить" конкурирующие инстансы
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔌 Webhook удалён")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить webhook: {e}")

    # Небольшая пауза перед стартом
    await asyncio.sleep(1)

    # Инициализируем базу данных
    await init_db()

    # Запускаем планировщик
    setup_scheduler(bot, async_session)

    # Запускаем OAuth сервер если настроен
    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        try:
            from oauth_server import run_oauth_server
            _oauth_runner = await run_oauth_server(port=8080)
            logger.info("🌐 OAuth сервер запущен на порту 8080")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось запустить OAuth сервер: {e}")
    else:
        logger.info("ℹ️ OAuth не настроен — используется общий календарь")

    # Запускаем Booking сервер
    try:
        _booking_server = asyncio.create_task(run_booking_server())
        logger.info("📅 Booking сервер запущен на порту 8082")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось запустить Booking сервер: {e}")

    logger.info("🚀 Бот успешно запущен!")


async def on_shutdown():
    """Действия при остановке бота"""
    global _oauth_runner, _booking_server

    # Останавливаем OAuth сервер
    if _oauth_runner:
        await _oauth_runner.cleanup()
        logger.info("🌐 OAuth сервер остановлен")

    # Останавливаем Booking сервер
    if _booking_server:
        _booking_server.cancel()
        logger.info("📅 Booking сервер остановлен")

    logger.info("👋 Бот остановлен")


async def main():
    """Главная функция"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    # Регистрация обработчиков
    # ВАЖНО: tunnel.router регистрируем ПЕРЕД user, чтобы команда /tunnel не уходила в AI
    dp.include_router(tunnel.router)
    user.register_handlers_user(dp)

    # Запуск
    await on_startup()

    try:
        logger.info("▶️ Запуск polling...")
        # allowed_updates можно настроить, handle_signals для graceful shutdown
        await dp.start_polling(
            bot,
            skip_updates=True,
            handle_signals=True,  # Graceful shutdown по Ctrl+C
        )
    finally:
        await on_shutdown()


if __name__ == "__main__":
    # Проверяем, что бот не запущен
    _pid_lock = check_already_running()
    asyncio.run(main())
