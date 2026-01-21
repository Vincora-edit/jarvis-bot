"""
Тесты для PromoService — промокоды и скидки
"""
import pytest
from datetime import datetime, timedelta

from database.models import PromoCode, User
from services.promo_service import PromoService


@pytest.fixture
async def subscription_promo(session):
    """Промокод на подписку"""
    promo = PromoCode(
        code="TEST30",
        description="30 дней basic",
        promo_type="subscription",
        plan="basic",
        days=30,
        max_uses=10,
        is_active=True
    )
    session.add(promo)
    await session.commit()
    return promo


@pytest.fixture
async def expired_promo(session):
    """Просроченный промокод"""
    promo = PromoCode(
        code="EXPIRED",
        description="Expired promo",
        promo_type="subscription",
        plan="basic",
        days=7,
        expires_at=datetime.utcnow() - timedelta(days=1),
        is_active=True
    )
    session.add(promo)
    await session.commit()
    return promo


class TestPromoValidation:
    """Тесты валидации промокодов"""

    @pytest.mark.asyncio
    async def test_valid_promo_code(self, session, test_user, subscription_promo):
        """Валидный промокод проходит проверку"""
        service = PromoService(session)
        result = await service.validate_promo("TEST30", test_user.id)
        assert result.success is True
        assert result.plan == "basic"
        assert result.days == 30

    @pytest.mark.asyncio
    async def test_invalid_promo_code(self, session, test_user):
        """Несуществующий промокод не проходит"""
        service = PromoService(session)
        result = await service.validate_promo("INVALID", test_user.id)
        assert result.success is False
        assert "не найден" in result.message.lower()

    @pytest.mark.asyncio
    async def test_expired_promo_code(self, session, test_user, expired_promo):
        """Просроченный промокод не проходит"""
        service = PromoService(session)
        result = await service.validate_promo("EXPIRED", test_user.id)
        assert result.success is False
        assert "истёк" in result.message.lower()

    @pytest.mark.asyncio
    async def test_case_insensitive_promo(self, session, test_user, subscription_promo):
        """Промокод регистронезависимый"""
        service = PromoService(session)

        # Нижний регистр
        result = await service.validate_promo("test30", test_user.id)
        assert result.success is True

        # Смешанный регистр
        result = await service.validate_promo("TeSt30", test_user.id)
        assert result.success is True


class TestPromoUsageLimits:
    """Тесты лимитов использования промокодов"""

    @pytest.mark.asyncio
    async def test_promo_max_uses_limit(self, session, subscription_promo):
        """Промокод с достигнутым лимитом не работает"""
        subscription_promo.current_uses = subscription_promo.max_uses
        await session.commit()

        # Создаём нового пользователя
        user = User(telegram_id=555555555, subscription_plan="free")
        session.add(user)
        await session.commit()

        service = PromoService(session)
        result = await service.validate_promo("TEST30", user.id)
        assert result.success is False
        assert "не действует" in result.message.lower()
