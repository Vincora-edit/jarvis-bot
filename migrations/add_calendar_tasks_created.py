"""
Миграция: Добавление поля calendar_tasks_created в таблицу daily_usages.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import async_session


async def migrate():
    """Добавление поля calendar_tasks_created"""
    async with async_session() as session:
        # Проверяем, есть ли уже поле
        result = await session.execute(text("PRAGMA table_info(daily_usages)"))
        columns = [row[1] for row in result.fetchall()]

        if "calendar_tasks_created" not in columns:
            await session.execute(text("""
                ALTER TABLE daily_usages ADD COLUMN calendar_tasks_created INTEGER DEFAULT 0
            """))
            await session.commit()
            print("✅ Поле calendar_tasks_created добавлено")
        else:
            print("ℹ️ Поле calendar_tasks_created уже существует")


if __name__ == "__main__":
    asyncio.run(migrate())
