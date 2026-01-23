"""
Миграция: Добавление таблицы scheduled_reminders для точных напоминаний.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import async_session


async def migrate():
    """Создание таблицы scheduled_reminders"""
    async with async_session() as session:
        # Создаём таблицу
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS scheduled_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                event_id VARCHAR(255) NOT NULL,
                event_title VARCHAR(500) NOT NULL,
                event_time DATETIME NOT NULL,
                remind_at DATETIME NOT NULL,
                minutes_before INTEGER NOT NULL,
                is_sent BOOLEAN DEFAULT 0 NOT NULL,
                sent_at DATETIME,
                job_id VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # Создаём индексы
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_scheduled_reminders_user_id
            ON scheduled_reminders(user_id)
        """))
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_scheduled_reminders_event_id
            ON scheduled_reminders(event_id)
        """))
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_scheduled_reminders_remind_at
            ON scheduled_reminders(remind_at)
        """))
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_scheduled_reminders_is_sent
            ON scheduled_reminders(is_sent)
        """))

        await session.commit()
        print("✅ Таблица scheduled_reminders создана")


if __name__ == "__main__":
    asyncio.run(migrate())
