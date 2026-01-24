"""
Тесты для LimitsService — проверка лимитов тарифных планов
"""
import pytest
from datetime import datetime

from services.limits_service import LimitsService
from database.models import Habit, BookingLink, DailyUsage


class TestHabitLimits:
    """Тесты лимитов привычек"""

    @pytest.mark.asyncio
    async def test_free_user_can_add_habit_under_limit(self, session, test_user):
        """Free пользователь может добавить привычку если лимит не превышен"""
        limits = LimitsService(session)
        can_add, message = await limits.can_add_habit(test_user.id)
        assert can_add is True
        assert message == "OK"

    @pytest.mark.asyncio
    async def test_free_user_cannot_exceed_habit_limit(self, session, user_with_habits):
        """Free пользователь не может превысить лимит в 1 привычку"""
        limits = LimitsService(session)
        can_add, message = await limits.can_add_habit(user_with_habits.id)
        assert can_add is False
        assert "1" in message  # Лимит 1 привычка
        assert "Бесплатный" in message

    @pytest.mark.asyncio
    async def test_pro_user_unlimited_habits(self, session, pro_user):
        """Pro пользователь имеет безлимит на привычки"""
        # Добавим 10 привычек
        for i in range(10):
            habit = Habit(
                user_id=pro_user.id,
                name=f"Habit {i+1}",
                emoji="✅",
                is_active=True
            )
            session.add(habit)
        await session.commit()

        limits = LimitsService(session)
        can_add, message = await limits.can_add_habit(pro_user.id)
        assert can_add is True


class TestAILimits:
    """Тесты лимитов AI запросов"""

    @pytest.mark.asyncio
    async def test_free_user_ai_limit(self, session, test_user):
        """Free пользователь имеет лимит 5 AI запросов в день"""
        limits = LimitsService(session)

        # Первый запрос — ок
        can_use, _ = await limits.can_use_ai(test_user.id)
        assert can_use is True

        # Симулируем 5 использований
        usage = await limits.get_or_create_daily_usage(test_user.id)
        usage.ai_requests = 5
        await session.commit()

        # 6-й запрос — нельзя
        can_use, message = await limits.can_use_ai(test_user.id)
        assert can_use is False
        assert "5" in message

    @pytest.mark.asyncio
    async def test_increment_ai_usage(self, session, test_user):
        """Проверка инкремента счётчика AI"""
        limits = LimitsService(session)

        await limits.increment_ai_usage(test_user.id)
        usage = await limits.get_or_create_daily_usage(test_user.id)
        assert usage.ai_requests == 1

        await limits.increment_ai_usage(test_user.id)
        await session.refresh(usage)
        assert usage.ai_requests == 2


class TestVPNLimits:
    """Тесты лимитов VPN"""

    @pytest.mark.asyncio
    async def test_free_user_can_activate_trial(self, session, test_user):
        """Free пользователь может активировать триал"""
        limits = LimitsService(session)
        can_use, message, devices = await limits.can_use_vpn(test_user.id)
        assert can_use is True
        assert message == "trial"
        assert devices == 1

    @pytest.mark.asyncio
    async def test_free_user_trial_already_used(self, session, test_user):
        """Free пользователь с использованным триалом без активной подписки"""
        test_user.vpn_trial_used = True
        test_user.vpn_trial_expires = datetime(2020, 1, 1)  # Истёк
        await session.commit()

        limits = LimitsService(session)
        can_use, message, devices = await limits.can_use_vpn(test_user.id)
        assert can_use is False
        assert devices == 0

    @pytest.mark.asyncio
    async def test_activate_vpn_trial(self, session, test_user):
        """Активация VPN триала"""
        limits = LimitsService(session)
        success, message = await limits.activate_vpn_trial(test_user.id)
        assert success is True
        assert "7" in message  # 7 дней триал

        await session.refresh(test_user)
        assert test_user.vpn_trial_used is True
        assert test_user.vpn_trial_expires is not None

    @pytest.mark.asyncio
    async def test_cannot_activate_trial_twice(self, session, test_user):
        """Нельзя активировать триал дважды"""
        limits = LimitsService(session)

        # Первая активация
        await limits.activate_vpn_trial(test_user.id)

        # Вторая попытка
        success, message = await limits.activate_vpn_trial(test_user.id)
        assert success is False
        assert "уже использовали" in message


class TestBookingLimits:
    """Тесты лимитов бронирования"""

    @pytest.mark.asyncio
    async def test_free_user_cannot_create_booking_link(self, session, test_user):
        """Free пользователь не может создавать ссылки бронирования"""
        limits = LimitsService(session)
        can_create, message = await limits.can_create_booking_link(test_user.id)
        assert can_create is False
        assert "недоступно" in message.lower()

    @pytest.mark.asyncio
    async def test_pro_user_can_create_booking_link(self, session, pro_user):
        """Pro пользователь может создавать ссылки бронирования"""
        limits = LimitsService(session)
        can_create, message = await limits.can_create_booking_link(pro_user.id)
        assert can_create is True


class TestUsageInfo:
    """Тесты получения информации об использовании"""

    @pytest.mark.asyncio
    async def test_get_usage_info_free_user(self, session, test_user):
        """Получение информации для free пользователя"""
        limits = LimitsService(session)
        info = await limits.get_usage_info(test_user.id)

        assert info["plan"] == "free"
        assert info["habits"]["limit"] == 1
        assert info["ai_requests"]["limit"] == 5
        assert info["booking_links"]["available"] is False

    @pytest.mark.asyncio
    async def test_get_usage_info_pro_user(self, session, pro_user):
        """Получение информации для pro пользователя"""
        limits = LimitsService(session)
        info = await limits.get_usage_info(pro_user.id)

        assert info["plan"] == "pro"
        assert info["habits"]["unlimited"] is True
        assert info["ai_requests"]["unlimited"] is True
        assert info["vpn_devices"]["limit"] == 5
