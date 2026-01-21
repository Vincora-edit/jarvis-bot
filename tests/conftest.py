"""
Pytest fixtures для тестов Jarvis Bot
"""
import pytest
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from database.models import Base, User, Habit, TunnelKey, BookingLink, DailyUsage


@pytest.fixture(scope="session")
def event_loop():
    """Создаём event loop для async тестов"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def async_engine():
    """In-memory SQLite для тестов"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine):
    """Async session для тестов"""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    async with async_session_maker() as session:
        yield session


@pytest.fixture
async def test_user(session):
    """Тестовый пользователь"""
    user = User(
        telegram_id=123456789,
        username="test_user",
        first_name="Test",
        subscription_plan="free",
        timezone="Europe/Moscow"
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def pro_user(session):
    """Пользователь с Pro тарифом"""
    user = User(
        telegram_id=987654321,
        username="pro_user",
        first_name="Pro",
        subscription_plan="pro",
        timezone="Europe/Moscow"
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def user_with_habits(session, test_user):
    """Пользователь с 3 привычками (лимит free)"""
    for i in range(3):
        habit = Habit(
            user_id=test_user.id,
            name=f"Habit {i+1}",
            emoji="✅",
            is_active=True
        )
        session.add(habit)
    await session.commit()
    return test_user


@pytest.fixture
async def user_with_vpn_key(session, test_user):
    """Пользователь с VPN ключом"""
    key = TunnelKey(
        user_id=test_user.id,
        xray_email="jarvis_123_d1",
        device_name="iPhone",
        subscription_url="vless://test",
        is_active=True
    )
    session.add(key)
    await session.commit()
    return test_user, key
