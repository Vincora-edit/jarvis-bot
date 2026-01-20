"""
Конфигурация приложения.
Все секреты загружаются из .env файла.
"""
import os
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()


class Config:
    """Основная конфигурация"""

    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    OWNER_TELEGRAM_ID: int = int(os.getenv("OWNER_TELEGRAM_ID", "0") or "0")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = "gpt-4o"
    WHISPER_MODEL: str = "whisper-1"

    # Google Service Account (для совместимости)
    GOOGLE_CREDENTIALS_FILE: str = "credentials.json"
    FINANCE_SHEET_NAME: str = os.getenv("FINANCE_SHEET_NAME", "Финансы")

    # Google OAuth (для индивидуальных календарей пользователей)
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8080/oauth/callback")

    # База данных
    DATABASE_URL: str = "sqlite:///bot_database.db"

    # Шифрование (для защиты переписок в БД)
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")

    # Таймзона
    TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")

    # Расписание (часы в формате 24h)
    MORNING_PLAN_HOUR: int = 8      # План дня
    EVENING_REFLECTION_HOUR: int = 21  # Вечерняя рефлексия
    WEEKLY_PLAN_HOUR: int = 9       # План на неделю (вс/пн)
    FOCUS_CHECK_HOURS: list = [10, 13, 16, 19]  # Фокус-чеки (каждые 3 часа)

    # Помодоро
    POMODORO_WORK_MINUTES: int = 25
    POMODORO_BREAK_MINUTES: int = 5
    POMODORO_LONG_BREAK_MINUTES: int = 15

    # Память AI (сколько сообщений хранить в контексте)
    CONVERSATION_HISTORY_LIMIT: int = 20

    @classmethod
    def validate(cls) -> bool:
        """Проверка обязательных переменных"""
        errors = []

        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN не установлен")
        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY не установлен")
        if not os.path.exists(cls.GOOGLE_CREDENTIALS_FILE):
            errors.append(f"Файл {cls.GOOGLE_CREDENTIALS_FILE} не найден")

        if errors:
            for error in errors:
                print(f"❌ Ошибка конфигурации: {error}")
            return False

        print("✅ Конфигурация загружена успешно")
        return True


# Создаём экземпляр конфигурации
config = Config()
