"""
Сервис бронирования встреч.
Логика создания ссылок, поиска слотов и бронирования.
"""
import secrets
import logging
from datetime import datetime, timedelta, date
from typing import Optional
import pytz

from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BookingLink, Booking, UserScheduleSettings, User
from services.calendar_service import CalendarService

logger = logging.getLogger(__name__)


class BookingService:
    """Сервис для работы с бронированиями"""

    def __init__(self, session: AsyncSession, user_timezone: str = None):
        self.session = session
        # Используем timezone пользователя или дефолтный
        tz_name = user_timezone or "Europe/Moscow"
        try:
            self.timezone = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            self.timezone = pytz.timezone("Europe/Moscow")

    @staticmethod
    def generate_slug(length: int = 8) -> str:
        """Генерация уникального slug для ссылки"""
        return secrets.token_urlsafe(length)[:length]

    @staticmethod
    def generate_cancel_token() -> str:
        """Генерация токена отмены"""
        return secrets.token_urlsafe(32)

    def _localize(self, dt: datetime) -> datetime:
        """Локализовать datetime если он naive"""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return self.timezone.localize(dt)
        return dt

    # === УПРАВЛЕНИЕ ССЫЛКАМИ ===

    async def create_booking_link(
        self,
        user_id: int,
        title: str,
        duration_minutes: int = 30,
        description: str = None,
        max_days_ahead: int = 14,
    ) -> BookingLink:
        """Создать ссылку для бронирования"""
        # Генерируем уникальный slug
        slug = self.generate_slug()

        # Проверяем уникальность
        while await self.get_booking_link_by_slug(slug):
            slug = self.generate_slug()

        link = BookingLink(
            user_id=user_id,
            slug=slug,
            title=title,
            duration_minutes=duration_minutes,
            description=description,
            max_days_ahead=max_days_ahead,
        )
        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)

        logger.info(f"Создана ссылка бронирования: {slug} для user_id={user_id}, period={max_days_ahead} days")
        return link

    async def get_booking_link_by_slug(self, slug: str) -> Optional[BookingLink]:
        """Получить ссылку по slug"""
        result = await self.session.execute(
            select(BookingLink)
            .options(selectinload(BookingLink.user))
            .where(BookingLink.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_user_booking_links(self, user_id: int) -> list[BookingLink]:
        """Получить все ссылки пользователя"""
        result = await self.session.execute(
            select(BookingLink)
            .where(BookingLink.user_id == user_id)
            .order_by(BookingLink.created_at.desc())
        )
        return list(result.scalars().all())

    async def deactivate_booking_link(self, link_id: int) -> bool:
        """Деактивировать ссылку"""
        result = await self.session.execute(
            select(BookingLink).where(BookingLink.id == link_id)
        )
        link = result.scalar_one_or_none()
        if link:
            link.is_active = False
            await self.session.commit()
            return True
        return False

    # === НАСТРОЙКИ РАСПИСАНИЯ ===

    async def get_user_schedule(self, user_id: int) -> Optional[UserScheduleSettings]:
        """Получить настройки расписания пользователя"""
        result = await self.session.execute(
            select(UserScheduleSettings).where(UserScheduleSettings.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def update_user_schedule(
        self,
        user_id: int,
        working_hours: dict = None,
        buffer_minutes: int = None,
        available_days: str = None,
    ) -> UserScheduleSettings:
        """Обновить или создать настройки расписания"""
        schedule = await self.get_user_schedule(user_id)

        if not schedule:
            schedule = UserScheduleSettings(user_id=user_id)
            self.session.add(schedule)

        if working_hours is not None:
            schedule.working_hours = working_hours
        if buffer_minutes is not None:
            schedule.buffer_minutes = buffer_minutes
        if available_days is not None:
            schedule.available_days = available_days

        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    # === СЛОТЫ ===

    def _get_work_hours_for_day(
        self,
        schedule: Optional[UserScheduleSettings],
        user: User,
        weekday: int,
    ) -> Optional[dict]:
        """
        Получить рабочие часы для конкретного дня недели.
        weekday: 0=понедельник, 6=воскресенье
        """
        day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        day_name = day_names[weekday]

        # Проверяем, доступен ли этот день
        if schedule:
            available = schedule.available_days.split(",")
            if str(weekday) not in available:
                return None
        else:
            # По умолчанию пн-пт
            if weekday > 4:
                return None

        # Если есть кастомные рабочие часы
        if schedule and schedule.working_hours and day_name in schedule.working_hours:
            return schedule.working_hours[day_name]

        # Иначе используем morning_time/evening_time из User
        return {
            "start": user.morning_time or "09:00",
            "end": user.evening_time or "18:00",
        }

    async def get_available_slots(
        self,
        booking_link: BookingLink,
        target_date: date,
    ) -> list[dict]:
        """
        Получить свободные слоты для бронирования на дату.

        Возвращает список слотов:
        [{"time": "10:00", "datetime": datetime}, ...]
        """
        user = booking_link.user

        # Проверяем, подключён ли календарь
        if not user.calendar_connected or not user.google_credentials:
            logger.warning(f"Календарь не подключён для user_id={user.id}")
            return []

        # Проверяем лимиты
        now = datetime.now(self.timezone)
        today = now.date()

        # Не раньше min_notice_hours
        min_datetime = now + timedelta(hours=booking_link.min_notice_hours)

        # Не позже max_days_ahead
        max_date = today + timedelta(days=booking_link.max_days_ahead)
        if target_date > max_date:
            return []

        # Если дата в прошлом
        if target_date < today:
            return []

        # Получаем настройки расписания
        schedule = await self.get_user_schedule(user.id)

        # Получаем рабочие часы для этого дня
        weekday = target_date.weekday()
        work_hours = self._get_work_hours_for_day(schedule, user, weekday)

        if not work_hours:
            return []  # День недоступен

        # Парсим время
        work_start_h, work_start_m = map(int, work_hours["start"].split(":"))
        work_end_h, work_end_m = map(int, work_hours["end"].split(":"))

        # Создаём границы дня
        day_start = self.timezone.localize(
            datetime.combine(target_date, datetime.min.time().replace(hour=work_start_h, minute=work_start_m))
        )
        day_end = self.timezone.localize(
            datetime.combine(target_date, datetime.min.time().replace(hour=work_end_h, minute=work_end_m))
        )

        # Если сегодня — не раньше min_notice_hours от текущего времени
        if target_date == today:
            if min_datetime > day_start:
                # Округляем до следующего получаса
                minutes = min_datetime.minute
                if minutes < 30:
                    day_start = min_datetime.replace(minute=30, second=0, microsecond=0)
                else:
                    day_start = (min_datetime + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        if day_start >= day_end:
            return []

        # Получаем события из Google Calendar
        try:
            calendar = CalendarService(
                user_credentials=user.google_credentials,
                user_timezone=user.timezone
            )
            free_slots = calendar.find_free_slots(
                date_str=target_date.strftime("%Y-%m-%d"),
                min_duration_minutes=booking_link.duration_minutes,
                work_start=work_start_h,
                work_end=work_end_h,
            )
        except Exception as e:
            logger.error(f"Ошибка получения слотов из календаря: {e}")
            return []

        # Получаем уже забронированные слоты
        existing_bookings = await self._get_bookings_for_date(booking_link.id, target_date)
        booked_times = {b.start_time for b in existing_bookings}

        # Проверяем лимит бронирований в день
        if len(existing_bookings) >= booking_link.max_bookings_per_day:
            return []

        # Генерируем конкретные слоты
        duration = booking_link.duration_minutes
        slot_step = 30  # Шаг между слотами — всегда 30 минут

        available_slots = []

        for free_slot in free_slots:
            slot_start = free_slot["start"]
            slot_end = free_slot["end"]

            # Округляем начало до ближайших 30 минут вверх
            minutes = slot_start.minute
            if minutes == 0 or minutes == 30:
                current = slot_start
            elif minutes < 30:
                current = slot_start.replace(minute=30, second=0, microsecond=0)
            else:
                current = (slot_start + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

            # Генерируем слоты внутри свободного окна с шагом 30 минут
            while current + timedelta(minutes=duration) <= slot_end:
                # Проверяем, что слот не пересекается с уже забронированными
                slot_end_time = current + timedelta(minutes=duration)
                is_booked = any(
                    not (slot_end_time <= self._localize(b.start_time) or current >= self._localize(b.end_time))
                    for b in existing_bookings
                )

                if not is_booked:
                    # Проверяем min_notice
                    if current >= min_datetime:
                        available_slots.append({
                            "time": current.strftime("%H:%M"),
                            "datetime": current,
                        })

                # Следующий слот через 30 минут
                current = current + timedelta(minutes=slot_step)

        return available_slots

    async def _get_bookings_for_date(self, link_id: int, target_date: date) -> list[Booking]:
        """Получить все бронирования на дату"""
        day_start = self.timezone.localize(
            datetime.combine(target_date, datetime.min.time())
        )
        day_end = self.timezone.localize(
            datetime.combine(target_date + timedelta(days=1), datetime.min.time())
        )

        result = await self.session.execute(
            select(Booking).where(
                and_(
                    Booking.booking_link_id == link_id,
                    Booking.start_time >= day_start,
                    Booking.start_time < day_end,
                    Booking.status == "confirmed",
                )
            )
        )
        return list(result.scalars().all())

    # === БРОНИРОВАНИЕ ===

    async def create_booking(
        self,
        booking_link: BookingLink,
        start_time: datetime,
        guest_name: str,
        guest_email: str,
        guest_notes: str = None,
    ) -> Booking:
        """Создать бронирование и событие в Google Calendar"""
        user = booking_link.user
        duration = booking_link.duration_minutes
        end_time = start_time + timedelta(minutes=duration)

        # Создаём событие в Google Calendar
        google_event_id = None
        try:
            calendar = CalendarService(
                user_credentials=user.google_credentials,
                user_timezone=user.timezone
            )
            event = calendar.create_event(
                title=f"{booking_link.title} — {guest_name}",
                start_datetime=start_time,
                duration_minutes=duration,
                description=f"Гость: {guest_name}\nEmail: {guest_email}\n\n{guest_notes or ''}",
            )
            google_event_id = event.get("id")
            logger.info(f"Создано событие в календаре: {google_event_id}")
        except Exception as e:
            logger.error(f"Ошибка создания события в календаре: {e}")
            # Продолжаем без Google Calendar

        # Создаём запись в БД
        booking = Booking(
            booking_link_id=booking_link.id,
            guest_name=guest_name,
            guest_email=guest_email,
            guest_notes=guest_notes,
            start_time=start_time,
            end_time=end_time,
            google_event_id=google_event_id,
            cancel_token=self.generate_cancel_token(),
        )
        self.session.add(booking)
        await self.session.commit()
        await self.session.refresh(booking)

        logger.info(f"Создано бронирование #{booking.id} для {guest_email}")
        return booking

    async def get_booking_by_id(self, booking_id: int) -> Optional[Booking]:
        """Получить бронирование по ID"""
        result = await self.session.execute(
            select(Booking)
            .options(selectinload(Booking.booking_link).selectinload(BookingLink.user))
            .where(Booking.id == booking_id)
        )
        return result.scalar_one_or_none()

    async def get_booking_by_cancel_token(self, token: str) -> Optional[Booking]:
        """Получить бронирование по токену отмены"""
        result = await self.session.execute(
            select(Booking)
            .options(selectinload(Booking.booking_link).selectinload(BookingLink.user))
            .where(Booking.cancel_token == token)
        )
        return result.scalar_one_or_none()

    async def cancel_booking(self, booking: Booking) -> bool:
        """Отменить бронирование"""
        # Удаляем из Google Calendar
        if booking.google_event_id:
            try:
                user = booking.booking_link.user
                calendar = CalendarService(
                    user_credentials=user.google_credentials,
                    user_timezone=user.timezone
                )
                calendar.delete_event(booking.google_event_id)
                logger.info(f"Удалено событие из календаря: {booking.google_event_id}")
            except Exception as e:
                logger.error(f"Ошибка удаления события из календаря: {e}")

        booking.status = "cancelled"
        await self.session.commit()

        logger.info(f"Бронирование #{booking.id} отменено")
        return True

    # === ДОСТУПНЫЕ ДАТЫ ===

    async def get_available_dates(
        self,
        booking_link: BookingLink,
        num_days: int = None,
    ) -> list[dict]:
        """
        Получить список доступных дат для бронирования.
        Возвращает даты, в которые есть хотя бы один свободный слот.
        """
        user = booking_link.user
        schedule = await self.get_user_schedule(user.id)

        now = datetime.now(self.timezone)
        today = now.date()

        # Используем max_days_ahead из настроек ссылки
        if num_days is None:
            num_days = booking_link.max_days_ahead

        available_dates = []
        day_names_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        month_names_ru = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

        for i in range(num_days):
            check_date = today + timedelta(days=i)
            weekday = check_date.weekday()

            # Проверяем, доступен ли этот день по расписанию
            work_hours = self._get_work_hours_for_day(schedule, user, weekday)
            if not work_hours:
                continue

            # Проверяем, есть ли реально свободные слоты в этот день
            slots = await self.get_available_slots(booking_link, check_date)
            if not slots:
                continue

            available_dates.append({
                "date": check_date,
                "iso": check_date.isoformat(),
                "day": check_date.day,
                "weekday": day_names_ru[weekday],
                "month": month_names_ru[check_date.month - 1],
            })

        return available_dates
