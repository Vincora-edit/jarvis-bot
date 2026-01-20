"""
Сервис для работы с промокодами.
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, PromoCode, PromoCodeUsage, Subscription
from services.plans import PLAN_PRICES, get_plan_name


@dataclass
class PromoResult:
    """Результат применения промокода"""
    success: bool
    message: str
    promo_type: str = ""

    # Для подписки
    plan: str = ""
    days: int = 0

    # Для скидки
    discount_percent: int = 0
    discount_amount: int = 0  # В копейках
    is_permanent: bool = False

    # Расчёт цены
    original_price: int = 0
    final_price: int = 0


class PromoService:
    """Сервис для работы с промокодами"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_promo_by_code(self, code: str) -> Optional[PromoCode]:
        """Получить промокод по коду"""
        result = await self.session.execute(
            select(PromoCode).where(
                PromoCode.code == code.upper(),
                PromoCode.is_active == True
            )
        )
        return result.scalar_one_or_none()

    async def validate_promo(
        self,
        code: str,
        user_id: int,
        plan: str = None,
        months: int = 1
    ) -> PromoResult:
        """
        Проверить валидность промокода.
        Возвращает PromoResult с информацией о скидке/подписке.
        """
        promo = await self.get_promo_by_code(code)

        if not promo:
            return PromoResult(success=False, message="Промокод не найден")

        # Проверка срока действия
        if promo.expires_at and promo.expires_at < datetime.utcnow():
            return PromoResult(success=False, message="Срок действия промокода истёк")

        # Проверка лимита использований
        if promo.max_uses and promo.current_uses >= promo.max_uses:
            return PromoResult(success=False, message="Промокод больше не действует")

        # Получаем пользователя
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return PromoResult(success=False, message="Пользователь не найден")

        # Проверка использования этим пользователем
        usage_count = await self.session.execute(
            select(func.count(PromoCodeUsage.id)).where(
                PromoCodeUsage.promo_code_id == promo.id,
                PromoCodeUsage.user_id == user_id
            )
        )
        user_uses = usage_count.scalar() or 0
        if user_uses >= promo.max_uses_per_user:
            return PromoResult(success=False, message="Вы уже использовали этот промокод")

        # Проверка только для новых пользователей
        if promo.new_users_only:
            # Проверяем был ли у пользователя платёж
            has_payments = await self.session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.user_id == user_id,
                    Subscription.plan != "free",
                    Subscription.plan != "free_trial"
                )
            )
            if (has_payments.scalar() or 0) > 0:
                return PromoResult(success=False, message="Промокод только для новых пользователей")

        # Проверка минимального периода
        if promo.min_months > 0 and months < promo.min_months:
            return PromoResult(
                success=False,
                message=f"Промокод действует при подписке от {promo.min_months} мес."
            )

        # Проверка применимости к плану
        if promo.applies_to_plans and plan:
            allowed_plans = [p.strip() for p in promo.applies_to_plans.split(",")]
            if plan not in allowed_plans:
                plans_text = ", ".join([get_plan_name(p) for p in allowed_plans])
                return PromoResult(
                    success=False,
                    message=f"Промокод действует только для: {plans_text}"
                )

        # === ОБРАБОТКА ПО ТИПУ ===

        if promo.promo_type == "subscription":
            # Даёт подписку бесплатно
            return PromoResult(
                success=True,
                message=f"Промокод даёт {get_plan_name(promo.plan)} на {promo.days} дней!",
                promo_type="subscription",
                plan=promo.plan,
                days=promo.days
            )

        elif promo.promo_type == "discount_percent":
            # Скидка в процентах
            if not plan:
                return PromoResult(
                    success=True,
                    message=f"Скидка {promo.discount_percent}%{'(постоянная)' if promo.discount_permanent else ''}!",
                    promo_type="discount_percent",
                    discount_percent=promo.discount_percent,
                    is_permanent=promo.discount_permanent
                )

            # Расчёт с ценой
            original = PLAN_PRICES.get(plan, {}).get(str(months), 0)
            discount = int(original * promo.discount_percent / 100)
            final = original - discount

            perm_text = " (постоянная)" if promo.discount_permanent else ""
            return PromoResult(
                success=True,
                message=f"Скидка {promo.discount_percent}%{perm_text}! Цена: {final // 100}₽ вместо {original // 100}₽",
                promo_type="discount_percent",
                discount_percent=promo.discount_percent,
                discount_amount=discount,
                is_permanent=promo.discount_permanent,
                original_price=original,
                final_price=final
            )

        elif promo.promo_type == "discount_fixed":
            # Фиксированная скидка
            if not plan:
                return PromoResult(
                    success=True,
                    message=f"Скидка {promo.discount_amount // 100}₽!",
                    promo_type="discount_fixed",
                    discount_amount=promo.discount_amount
                )

            original = PLAN_PRICES.get(plan, {}).get(str(months), 0)
            final = max(0, original - promo.discount_amount)

            return PromoResult(
                success=True,
                message=f"Скидка {promo.discount_amount // 100}₽! Цена: {final // 100}₽ вместо {original // 100}₽",
                promo_type="discount_fixed",
                discount_amount=promo.discount_amount,
                original_price=original,
                final_price=final
            )

        elif promo.promo_type == "trial_extend":
            # Продление триала
            return PromoResult(
                success=True,
                message=f"Продление пробного периода на {promo.days} дней!",
                promo_type="trial_extend",
                days=promo.days
            )

        return PromoResult(success=False, message="Неизвестный тип промокода")

    async def apply_promo(
        self,
        code: str,
        user_id: int,
        plan: str = None,
        months: int = 1,
        subscription_id: int = None
    ) -> PromoResult:
        """
        Применить промокод.
        Записывает использование и возвращает результат.
        """
        # Сначала валидируем
        result = await self.validate_promo(code, user_id, plan, months)
        if not result.success:
            return result

        promo = await self.get_promo_by_code(code)
        user_result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one()

        # Записываем использование
        usage = PromoCodeUsage(
            promo_code_id=promo.id,
            user_id=user_id,
            subscription_id=subscription_id,
            discount_applied=result.discount_amount,
            original_price=result.original_price,
            final_price=result.final_price
        )
        self.session.add(usage)

        # Увеличиваем счётчик
        promo.current_uses += 1

        # Если постоянная скидка — сохраняем в профиль
        if result.is_permanent and result.discount_percent > 0:
            # Берём максимальную скидку
            if result.discount_percent > user.permanent_discount_percent:
                user.permanent_discount_percent = result.discount_percent
                user.permanent_discount_promo_id = promo.id

        await self.session.commit()
        return result

    async def get_user_discount(self, user_id: int) -> int:
        """Получить постоянную скидку пользователя в %"""
        result = await self.session.execute(
            select(User.permanent_discount_percent).where(User.id == user_id)
        )
        return result.scalar() or 0

    async def calculate_price(
        self,
        user_id: int,
        plan: str,
        months: int,
        promo_code: str = None
    ) -> Tuple[int, int, str]:
        """
        Рассчитать цену с учётом скидок.
        Возвращает: (original_price, final_price, discount_info)
        """
        original = PLAN_PRICES.get(plan, {}).get(str(months), 0)
        final = original
        discount_info = ""

        # Сначала применяем постоянную скидку пользователя
        user_discount = await self.get_user_discount(user_id)
        if user_discount > 0:
            discount = int(original * user_discount / 100)
            final -= discount
            discount_info = f"Ваша скидка {user_discount}%"

        # Потом промокод (если есть)
        if promo_code:
            result = await self.validate_promo(promo_code, user_id, plan, months)
            if result.success and result.discount_amount > 0:
                final = result.final_price
                if discount_info:
                    discount_info += f" + промокод"
                else:
                    discount_info = f"Промокод -{result.discount_percent}%"

        return original, max(0, final), discount_info


# === УТИЛИТЫ ДЛЯ СОЗДАНИЯ ПРОМОКОДОВ ===

async def create_subscription_promo(
    session: AsyncSession,
    code: str,
    plan: str,
    days: int,
    description: str,
    max_uses: int = None
) -> PromoCode:
    """Создать промокод на бесплатную подписку"""
    promo = PromoCode(
        code=code.upper(),
        description=description,
        promo_type="subscription",
        plan=plan,
        days=days,
        max_uses=max_uses
    )
    session.add(promo)
    await session.commit()
    return promo


async def create_discount_promo(
    session: AsyncSession,
    code: str,
    discount_percent: int,
    description: str,
    permanent: bool = False,
    new_users_only: bool = False,
    applies_to_plans: str = None,
    max_uses: int = None,
    expires_at: datetime = None
) -> PromoCode:
    """Создать промокод на скидку"""
    promo = PromoCode(
        code=code.upper(),
        description=description,
        promo_type="discount_percent",
        discount_percent=discount_percent,
        discount_permanent=permanent,
        new_users_only=new_users_only,
        applies_to_plans=applies_to_plans,
        max_uses=max_uses,
        expires_at=expires_at
    )
    session.add(promo)
    await session.commit()
    return promo
