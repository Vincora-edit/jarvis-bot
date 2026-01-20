"""
Сервис проверки и управления лимитами тарифов.
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, Habit, Reminder, BookingLink, DailyUsage
from services.plans import get_plan_limits, get_plan_name, is_limit_exceeded, PLANS


class LimitsService:
    """Сервис для проверки лимитов тарифных планов"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_plan(self, user_id: int) -> str:
        """Получить тариф пользователя"""
        result = await self.session.execute(
            select(User.subscription_plan).where(User.id == user_id)
        )
        plan = result.scalar_one_or_none()
        return plan or "free"

    async def get_or_create_daily_usage(self, user_id: int) -> DailyUsage:
        """Получить или создать запись дневного использования"""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        result = await self.session.execute(
            select(DailyUsage).where(
                DailyUsage.user_id == user_id,
                DailyUsage.date == today
            )
        )
        usage = result.scalar_one_or_none()

        if not usage:
            usage = DailyUsage(user_id=user_id, date=today)
            self.session.add(usage)
            await self.session.flush()

        return usage

    # === ПРОВЕРКИ ЛИМИТОВ ===

    async def can_add_habit(self, user_id: int) -> Tuple[bool, str]:
        """Проверить можно ли добавить привычку"""
        plan = await self.get_user_plan(user_id)
        limits = get_plan_limits(plan)

        # Считаем текущие активные привычки
        result = await self.session.execute(
            select(func.count(Habit.id)).where(
                Habit.user_id == user_id,
                Habit.is_active == True
            )
        )
        current_count = result.scalar() or 0

        if is_limit_exceeded(current_count, limits.habits_max):
            plan_name = get_plan_name(plan)
            return False, f"Достигнут лимит привычек ({limits.habits_max}) для плана «{plan_name}». Перейдите на более высокий тариф."

        return True, "OK"

    async def can_create_reminder(self, user_id: int) -> Tuple[bool, str]:
        """Проверить можно ли создать напоминание"""
        plan = await self.get_user_plan(user_id)
        limits = get_plan_limits(plan)

        usage = await self.get_or_create_daily_usage(user_id)

        if is_limit_exceeded(usage.reminders_created, limits.reminders_per_day):
            plan_name = get_plan_name(plan)
            return False, f"Достигнут дневной лимит напоминаний ({limits.reminders_per_day}) для плана «{plan_name}»."

        return True, "OK"

    async def can_use_ai(self, user_id: int) -> Tuple[bool, str]:
        """Проверить можно ли использовать AI"""
        plan = await self.get_user_plan(user_id)
        limits = get_plan_limits(plan)

        usage = await self.get_or_create_daily_usage(user_id)

        if is_limit_exceeded(usage.ai_requests, limits.ai_requests_per_day):
            plan_name = get_plan_name(plan)
            return False, f"Достигнут дневной лимит AI-запросов ({limits.ai_requests_per_day}) для плана «{plan_name}»."

        return True, "OK"

    async def can_create_booking_link(self, user_id: int) -> Tuple[bool, str]:
        """Проверить можно ли создать ссылку для бронирования"""
        plan = await self.get_user_plan(user_id)
        limits = get_plan_limits(plan)

        if limits.booking_links_max == 0:
            plan_name = get_plan_name(plan)
            return False, f"Бронирование недоступно для плана «{plan_name}». Перейдите на Базовый или выше."

        # Считаем текущие активные ссылки
        result = await self.session.execute(
            select(func.count(BookingLink.id)).where(
                BookingLink.user_id == user_id,
                BookingLink.is_active == True
            )
        )
        current_count = result.scalar() or 0

        if is_limit_exceeded(current_count, limits.booking_links_max):
            plan_name = get_plan_name(plan)
            return False, f"Достигнут лимит ссылок для бронирования ({limits.booking_links_max}) для плана «{plan_name}»."

        return True, "OK"

    async def can_use_vpn(self, user_id: int) -> Tuple[bool, str, int]:
        """
        Проверить можно ли использовать VPN.
        Возвращает: (можно_ли, сообщение, кол-во_устройств)
        """
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return False, "Пользователь не найден", 0

        plan = user.subscription_plan or "free"
        limits = get_plan_limits(plan)

        # Для free — проверяем триал
        if plan == "free":
            if limits.vpn_trial_days > 0 and not user.vpn_trial_used:
                # Можно активировать триал
                return True, "trial", 1  # 1 устройство на триал

            if user.vpn_trial_used and user.vpn_trial_expires:
                if user.vpn_trial_expires > datetime.utcnow():
                    # Триал ещё активен
                    days_left = (user.vpn_trial_expires - datetime.utcnow()).days
                    return True, f"trial_active:{days_left}", 1

            return False, "VPN недоступен для бесплатного плана. Оформите подписку или активируйте триал.", 0

        # Для платных планов
        if limits.vpn_devices == 0:
            return False, "VPN недоступен для вашего плана.", 0

        return True, "OK", limits.vpn_devices

    # === ИНКРЕМЕНТЫ СЧЁТЧИКОВ ===

    async def increment_ai_usage(self, user_id: int) -> None:
        """Увеличить счётчик AI запросов"""
        usage = await self.get_or_create_daily_usage(user_id)
        usage.ai_requests += 1
        await self.session.commit()

    async def increment_reminder_usage(self, user_id: int) -> None:
        """Увеличить счётчик напоминаний"""
        usage = await self.get_or_create_daily_usage(user_id)
        usage.reminders_created += 1
        await self.session.commit()

    async def increment_calendar_reminder_usage(self, user_id: int) -> None:
        """Увеличить счётчик напоминаний календаря"""
        usage = await self.get_or_create_daily_usage(user_id)
        usage.calendar_reminders += 1
        await self.session.commit()

    # === ТРИАЛ VPN ===

    async def activate_vpn_trial(self, user_id: int) -> Tuple[bool, str]:
        """Активировать VPN триал для пользователя"""
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return False, "Пользователь не найден"

        if user.vpn_trial_used:
            return False, "Вы уже использовали пробный период VPN"

        limits = get_plan_limits("free")
        if limits.vpn_trial_days == 0:
            return False, "Пробный период недоступен"

        user.vpn_trial_used = True
        user.vpn_trial_expires = datetime.utcnow() + timedelta(days=limits.vpn_trial_days)
        await self.session.commit()

        return True, f"Пробный период на {limits.vpn_trial_days} дней активирован!"

    # === ИНФОРМАЦИЯ О ЛИМИТАХ ===

    async def get_usage_info(self, user_id: int) -> dict:
        """Получить информацию об использовании лимитов"""
        plan = await self.get_user_plan(user_id)
        limits = get_plan_limits(plan)
        usage = await self.get_or_create_daily_usage(user_id)

        # Считаем привычки
        result = await self.session.execute(
            select(func.count(Habit.id)).where(
                Habit.user_id == user_id,
                Habit.is_active == True
            )
        )
        habits_count = result.scalar() or 0

        # Считаем ссылки бронирования
        result = await self.session.execute(
            select(func.count(BookingLink.id)).where(
                BookingLink.user_id == user_id,
                BookingLink.is_active == True
            )
        )
        booking_links_count = result.scalar() or 0

        return {
            "plan": plan,
            "plan_name": get_plan_name(plan),
            "habits": {
                "used": habits_count,
                "limit": limits.habits_max,
                "unlimited": limits.habits_max == 0
            },
            "ai_requests": {
                "used": usage.ai_requests,
                "limit": limits.ai_requests_per_day,
                "unlimited": limits.ai_requests_per_day == 0
            },
            "reminders": {
                "used": usage.reminders_created,
                "limit": limits.reminders_per_day,
                "unlimited": limits.reminders_per_day == 0
            },
            "calendar_reminders": {
                "used": usage.calendar_reminders,
                "limit": limits.calendar_reminders_per_day,
                "unlimited": limits.calendar_reminders_per_day == 0
            },
            "booking_links": {
                "used": booking_links_count,
                "limit": limits.booking_links_max,
                "unlimited": limits.booking_links_max == 0,
                "available": limits.booking_links_max > 0
            },
            "vpn_devices": limits.vpn_devices,
            "analytics": {
                "enabled": limits.analytics_enabled,
                "weekly": limits.analytics_weekly,
                "ai_insights": limits.analytics_ai_insights
            }
        }
