"""
Сервис реферальной системы.
"""
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, Subscription


# Конфигурация реферальной системы
REFERRAL_REWARD_DAYS = 14  # Дней подписки за оплатившего реферала
REFERRAL_CODE_LENGTH = 8   # Длина реферального кода


def generate_referral_code() -> str:
    """Генерация уникального реферального кода"""
    chars = string.ascii_uppercase + string.digits
    # Убираем похожие символы (O, 0, I, 1, L)
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '').replace('L', '')
    return ''.join(secrets.choice(chars) for _ in range(REFERRAL_CODE_LENGTH))


class ReferralService:
    """Сервис для работы с реферальной системой"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_referral_code(self, user_id: int) -> str:
        """Получить или создать реферальный код для пользователя"""
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return None

        if user.referral_code:
            return user.referral_code

        # Генерируем уникальный код
        for _ in range(10):  # 10 попыток
            code = generate_referral_code()
            # Проверяем уникальность
            existing = await self.session.execute(
                select(User).where(User.referral_code == code)
            )
            if not existing.scalar_one_or_none():
                user.referral_code = code
                await self.session.commit()
                return code

        return None

    async def get_user_by_referral_code(self, code: str) -> Optional[User]:
        """Найти пользователя по реферальному коду"""
        result = await self.session.execute(
            select(User).where(User.referral_code == code.upper())
        )
        return result.scalar_one_or_none()

    async def register_referral(self, new_user_id: int, referral_code: str) -> Tuple[bool, str]:
        """
        Зарегистрировать реферала (при регистрации нового пользователя).
        Награда начисляется позже, при первом платеже.
        """
        # Находим пригласившего
        referrer = await self.get_user_by_referral_code(referral_code)
        if not referrer:
            return False, "Реферальный код не найден"

        # Получаем нового пользователя
        result = await self.session.execute(
            select(User).where(User.id == new_user_id)
        )
        new_user = result.scalar_one_or_none()

        if not new_user:
            return False, "Пользователь не найден"

        # Нельзя пригласить самого себя
        if referrer.id == new_user.id:
            return False, "Нельзя использовать свой код"

        # Проверяем, не привязан ли уже
        if new_user.referred_by_user_id:
            return False, "Вы уже зарегистрированы по реферальной ссылке"

        # Привязываем
        new_user.referred_by_user_id = referrer.id
        await self.session.commit()

        return True, f"Вы зарегистрированы по приглашению от {referrer.first_name or referrer.username or 'друга'}!"

    async def process_referral_reward(self, user_id: int) -> Optional[Tuple[int, int]]:
        """
        Начислить награду пригласившему при первом платеже реферала.
        Возвращает (referrer_user_id, bonus_days) или None если награда уже была.
        """
        # Получаем пользователя
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user or not user.referred_by_user_id:
            return None

        # Проверяем, была ли уже награда (первый платёж)
        # Смотрим количество платежей пользователя
        payments_count = await self.session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.user_id == user_id,
                Subscription.status == "active"
            )
        )
        count = payments_count.scalar() or 0

        # Если это не первый платёж — награда уже была
        if count > 1:
            return None

        # Находим пригласившего
        result = await self.session.execute(
            select(User).where(User.id == user.referred_by_user_id)
        )
        referrer = result.scalar_one_or_none()

        if not referrer:
            return None

        # Начисляем бонусные дни
        referrer.referral_bonus_days += REFERRAL_REWARD_DAYS
        referrer.referral_count += 1
        await self.session.commit()

        return referrer.id, REFERRAL_REWARD_DAYS

    async def get_referral_stats(self, user_id: int) -> dict:
        """Получить статистику рефералов пользователя"""
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return {}

        # Считаем всех приглашённых (зарегистрированных)
        invited_count = await self.session.execute(
            select(func.count(User.id)).where(User.referred_by_user_id == user_id)
        )
        total_invited = invited_count.scalar() or 0

        # Оплатившие = referral_count
        paid_count = user.referral_count

        return {
            "referral_code": user.referral_code,
            "total_invited": total_invited,  # Всего зарегистрировалось
            "paid_count": paid_count,        # Оплатили
            "bonus_days": user.referral_bonus_days,  # Накопленные дни
            "reward_per_referral": REFERRAL_REWARD_DAYS,
        }

    async def use_bonus_days(self, user_id: int, days: int) -> Tuple[bool, str]:
        """Использовать накопленные бонусные дни"""
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return False, "Пользователь не найден"

        if user.referral_bonus_days < days:
            return False, f"Недостаточно бонусных дней. У вас: {user.referral_bonus_days}"

        user.referral_bonus_days -= days
        await self.session.commit()

        return True, f"Использовано {days} бонусных дней"

    async def get_referrals_list(self, user_id: int, limit: int = 10) -> list:
        """Получить список приглашённых пользователей"""
        result = await self.session.execute(
            select(User).where(User.referred_by_user_id == user_id)
            .order_by(User.created_at.desc())
            .limit(limit)
        )
        users = result.scalars().all()

        referrals = []
        for u in users:
            # Проверяем, оплатил ли
            payments = await self.session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.user_id == u.id,
                    Subscription.status == "active"
                )
            )
            has_paid = (payments.scalar() or 0) > 0

            referrals.append({
                "username": u.username or u.first_name or f"ID:{u.telegram_id}",
                "created_at": u.created_at,
                "has_paid": has_paid,
            })

        return referrals
