"""
Сервис автоочистки старых данных.
Запускается раз в сутки для экономии места в БД.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Conversation, Reminder, HabitLog, Booking

logger = logging.getLogger(__name__)


# Настройки хранения (в днях)
RETENTION_DAYS = {
    "conversations": 7,      # История сообщений — 7 дней
    "reminders_sent": 30,    # Отправленные напоминания — 30 дней
    "habit_logs": 90,        # Логи привычек — 90 дней (для статистики)
    "bookings_old": 90,      # Прошедшие бронирования — 90 дней
}


class CleanupService:
    """Сервис для очистки устаревших данных"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def cleanup_all(self) -> dict:
        """
        Запустить полную очистку.
        Возвращает статистику удалённых записей.
        """
        stats = {}

        stats["conversations"] = await self._cleanup_conversations()
        stats["reminders"] = await self._cleanup_reminders()
        stats["habit_logs"] = await self._cleanup_habit_logs()
        stats["bookings"] = await self._cleanup_bookings()

        total = sum(stats.values())
        if total > 0:
            logger.info(f"🧹 Очистка БД: удалено {total} записей: {stats}")

        return stats

    async def _cleanup_conversations(self) -> int:
        """Удалить старые сообщения"""
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS["conversations"])

        result = await self.session.execute(
            delete(Conversation).where(Conversation.created_at < cutoff)
        )
        await self.session.commit()

        count = result.rowcount
        if count > 0:
            logger.debug(f"Удалено conversations: {count}")
        return count

    async def _cleanup_reminders(self) -> int:
        """Удалить старые отправленные напоминания"""
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS["reminders_sent"])

        result = await self.session.execute(
            delete(Reminder).where(
                and_(
                    Reminder.is_sent == True,
                    Reminder.created_at < cutoff
                )
            )
        )
        await self.session.commit()

        count = result.rowcount
        if count > 0:
            logger.debug(f"Удалено reminders: {count}")
        return count

    async def _cleanup_habit_logs(self) -> int:
        """Удалить старые логи привычек"""
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS["habit_logs"])

        result = await self.session.execute(
            delete(HabitLog).where(HabitLog.date < cutoff)
        )
        await self.session.commit()

        count = result.rowcount
        if count > 0:
            logger.debug(f"Удалено habit_logs: {count}")
        return count

    async def _cleanup_bookings(self) -> int:
        """Удалить старые прошедшие бронирования"""
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS["bookings_old"])

        result = await self.session.execute(
            delete(Booking).where(
                and_(
                    Booking.end_time < cutoff,
                    Booking.status.in_(["completed", "cancelled"])
                )
            )
        )
        await self.session.commit()

        count = result.rowcount
        if count > 0:
            logger.debug(f"Удалено bookings: {count}")
        return count
