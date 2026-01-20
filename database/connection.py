"""
Подключение к базе данных SQLite.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker

from config import config
from .models import Base


# Создаём асинхронный движок
# SQLite URL: sqlite+aiosqlite:///bot_database.db
DATABASE_URL = config.DATABASE_URL.replace("sqlite://", "sqlite+aiosqlite://")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # True для дебага SQL запросов
)

# Фабрика сессий
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Инициализация базы данных (создание таблиц)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ База данных инициализирована")


async def get_session() -> AsyncSession:
    """Получить сессию для работы с БД"""
    async with async_session() as session:
        yield session
