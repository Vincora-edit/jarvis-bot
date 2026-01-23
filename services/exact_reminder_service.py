"""
Сервис точных напоминаний.
Планирует напоминания на конкретное время через APScheduler.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz

from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import ScheduledReminder, User
from services.smart_reminder_service import SmartReminderService, EVENT_CATEGORIES

logger = logging.getLogger(__name__)

# Глобальная ссылка на scheduler (устанавливается при старте бота)
_scheduler = None
_bot = None


def init_exact_reminders(scheduler, bot):
    """Инициализация сервиса — вызывается при старте бота"""
    global _scheduler, _bot
    _scheduler = scheduler
    _bot = bot
    logger.info("✅ ExactReminderService инициализирован")


class ExactReminderService:
    """Сервис для планирования точных напоминаний"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.tz = pytz.timezone(config.TIMEZONE)
        self.smart_service = SmartReminderService()

    async def schedule_reminders_for_event(
        self,
        user_id: int,
        telegram_id: int,
        event_id: str,
        event_title: str,
        event_time: datetime,
        remind_minutes: list[int] = None,
    ) -> list[ScheduledReminder]:
        """
        Запланировать напоминания для события.

        Args:
            user_id: ID пользователя в базе
            telegram_id: Telegram ID для отправки
            event_id: Google Calendar event ID
            event_title: Название события
            event_time: Время события (с timezone)
            remind_minutes: За сколько минут напомнить [60, 15]

        Returns:
            Список созданных ScheduledReminder
        """
        if remind_minutes is None:
            remind_minutes = self.smart_service.get_reminder_times(event_title)

        # Убедимся что event_time имеет timezone
        if event_time.tzinfo is None:
            event_time = self.tz.localize(event_time)

        now = datetime.now(self.tz)
        created_reminders = []

        for minutes in remind_minutes:
            remind_at = event_time - timedelta(minutes=minutes)

            # Пропускаем если время напоминания уже прошло
            if remind_at <= now:
                logger.debug(f"Пропуск напоминания за {minutes} мин — время прошло")
                continue

            # Проверяем, не запланировано ли уже
            existing = await self.session.execute(
                select(ScheduledReminder).where(
                    and_(
                        ScheduledReminder.user_id == user_id,
                        ScheduledReminder.event_id == event_id,
                        ScheduledReminder.minutes_before == minutes,
                        ScheduledReminder.is_sent == False,
                    )
                )
            )
            if existing.scalar_one_or_none():
                logger.debug(f"Напоминание за {minutes} мин уже запланировано")
                continue

            # Создаём запись в базе
            job_id = f"reminder_{user_id}_{event_id}_{minutes}"
            reminder = ScheduledReminder(
                user_id=user_id,
                event_id=event_id,
                event_title=event_title,
                event_time=event_time,
                remind_at=remind_at,
                minutes_before=minutes,
                job_id=job_id,
            )
            self.session.add(reminder)
            await self.session.flush()  # Получаем ID

            # Планируем job в APScheduler
            if _scheduler and _bot:
                try:
                    _scheduler.add_job(
                        send_exact_reminder,
                        trigger="date",
                        run_date=remind_at,
                        args=[telegram_id, reminder.id],
                        id=job_id,
                        replace_existing=True,
                        misfire_grace_time=300,  # 5 минут grace period
                    )
                    logger.info(f"📅 Запланировано напоминание: {event_title} за {minutes} мин на {remind_at.strftime('%H:%M')}")
                except Exception as e:
                    logger.error(f"Ошибка планирования job: {e}")

            created_reminders.append(reminder)

        await self.session.commit()
        return created_reminders

    async def cancel_reminders_for_event(self, user_id: int, event_id: str):
        """Отменить все напоминания для события (при удалении/изменении)"""
        # Находим все неотправленные напоминания
        result = await self.session.execute(
            select(ScheduledReminder).where(
                and_(
                    ScheduledReminder.user_id == user_id,
                    ScheduledReminder.event_id == event_id,
                    ScheduledReminder.is_sent == False,
                )
            )
        )
        reminders = result.scalars().all()

        for reminder in reminders:
            # Удаляем job из scheduler
            if _scheduler and reminder.job_id:
                try:
                    _scheduler.remove_job(reminder.job_id)
                except Exception:
                    pass  # Job может уже не существовать

        # Удаляем записи из базы
        await self.session.execute(
            delete(ScheduledReminder).where(
                and_(
                    ScheduledReminder.user_id == user_id,
                    ScheduledReminder.event_id == event_id,
                    ScheduledReminder.is_sent == False,
                )
            )
        )
        await self.session.commit()

        if reminders:
            logger.info(f"🗑️ Отменено {len(reminders)} напоминаний для события {event_id}")

    async def reschedule_for_event(
        self,
        user_id: int,
        telegram_id: int,
        event_id: str,
        event_title: str,
        new_event_time: datetime,
    ):
        """Перепланировать напоминания при изменении времени события"""
        # Отменяем старые
        await self.cancel_reminders_for_event(user_id, event_id)

        # Планируем новые
        await self.schedule_reminders_for_event(
            user_id=user_id,
            telegram_id=telegram_id,
            event_id=event_id,
            event_title=event_title,
            event_time=new_event_time,
        )

    async def cleanup_old_reminders(self, days: int = 7):
        """Удалить старые отправленные напоминания"""
        cutoff = datetime.utcnow() - timedelta(days=days)
        await self.session.execute(
            delete(ScheduledReminder).where(
                and_(
                    ScheduledReminder.is_sent == True,
                    ScheduledReminder.sent_at < cutoff,
                )
            )
        )
        await self.session.commit()


async def send_exact_reminder(telegram_id: int, reminder_id: int):
    """
    Функция отправки напоминания (вызывается APScheduler).
    Должна быть на уровне модуля для сериализации.
    """
    from database import async_session
    from services.memory_service import MemoryService

    logger.info(f"⏰ Отправка точного напоминания {reminder_id} для {telegram_id}")

    async with async_session() as session:
        # Получаем напоминание
        result = await session.execute(
            select(ScheduledReminder).where(ScheduledReminder.id == reminder_id)
        )
        reminder = result.scalar_one_or_none()

        if not reminder:
            logger.warning(f"Напоминание {reminder_id} не найдено")
            return

        if reminder.is_sent:
            logger.debug(f"Напоминание {reminder_id} уже отправлено")
            return

        # Формируем сообщение
        smart_service = SmartReminderService()
        tz = pytz.timezone(config.TIMEZONE)
        event_time = reminder.event_time
        if event_time.tzinfo is None:
            event_time = tz.localize(event_time)

        message = smart_service.generate_reminder(
            title=reminder.event_title,
            event_time=event_time,
            minutes_until=reminder.minutes_before,
            include_prep=True,
        )

        # Отправляем
        try:
            if _bot:
                await _bot.send_message(telegram_id, message)
                logger.info(f"✅ Напоминание отправлено: {reminder.event_title}")

                # Сохраняем в историю
                result = await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                user = result.scalar_one_or_none()
                if user:
                    memory = MemoryService(session)
                    await memory.save_message(
                        user.id,
                        "assistant",
                        f"[Напоминание: {reminder.event_title}] {message}",
                        "reminder"
                    )
        except Exception as e:
            logger.error(f"❌ Ошибка отправки напоминания: {e}")

        # Помечаем как отправленное
        reminder.is_sent = True
        reminder.sent_at = datetime.utcnow()
        await session.commit()
