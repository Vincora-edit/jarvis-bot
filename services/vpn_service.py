"""
VPN Service - новый сервис для работы с VPN.

Заменяет marzban_service.py, работает напрямую с Xray-core.
Поддерживает несколько серверов, failover, health checks.

Использование:
    from services.vpn_service import VPNService, get_vpn_service

    async with async_session() as session:
        vpn = VPNService(session)
        key, error = await vpn.create_key(user_id, telegram_id)
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TunnelKey, Subscription, User

# Импортируем наш VPN модуль
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vpn.config import get_config, VPNConfig, VPNServer
from vpn.xray_service import XrayManager
from vpn.key_generator import VLESSKeyGenerator, SubscriptionTokenGenerator

logger = logging.getLogger(__name__)


# Лимиты VPN по планам (синхронизированы с marzban_service для совместимости)
VPN_PLAN_LIMITS = {
    "free": {"max_keys": 0, "data_limit_gb": 0, "expire_days": 0},
    "free_trial": {"max_keys": 1, "data_limit_gb": 0, "expire_days": 14},
    "basic": {"max_keys": 3, "data_limit_gb": 0, "expire_days": 30},
    "standard": {"max_keys": 5, "data_limit_gb": 0, "expire_days": 30},
    "pro": {"max_keys": 10, "data_limit_gb": 0, "expire_days": 30},
}

# Алиас для обратной совместимости
PLAN_LIMITS = VPN_PLAN_LIMITS


def bytes_to_human(size: int) -> str:
    """Переводит байты в читаемый формат"""
    if size is None or size == 0:
        return "0 ГБ"
    for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ПБ"


@dataclass
class KeyResult:
    """Результат создания ключа"""
    success: bool
    vless_url: Optional[str] = None
    subscription_url: Optional[str] = None
    server_id: Optional[str] = None
    error: Optional[str] = None


class VPNService:
    """
    Сервис управления VPN.

    Основные функции:
    - Создание/удаление ключей
    - Проверка лимитов по планам
    - Получение статистики
    - Работа с несколькими серверами
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.config = get_config()
        self.xray = XrayManager(self.config)
        self.key_generator = VLESSKeyGenerator(self.config.subscription_secret)
        self.token_generator = SubscriptionTokenGenerator(self.config.subscription_secret)

        # Режим: native (свой Xray) или legacy (Marzban)
        # Если есть настроенные серверы с reality ключами — используем native
        self._use_native = any(
            s.reality_public_key and s.reality_private_key
            for s in self.config.servers
        )

        if self._use_native:
            logger.info("VPN: используем native режим (свой Xray)")
        else:
            logger.info("VPN: используем legacy режим (Marzban fallback)")

    # === ПОДПИСКИ ===

    async def get_user_subscription(self, user_id: int) -> Optional[Subscription]:
        """Получить активную подписку пользователя"""
        result = await self.session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == "active"
            ).order_by(Subscription.expires_at.desc())
        )
        return result.scalar_one_or_none()

    async def get_user_plan(self, user_id: int) -> str:
        """Получить текущий план пользователя"""
        sub = await self.get_user_subscription(user_id)
        if not sub:
            return "free"

        # Проверяем не истекла ли
        if sub.expires_at and sub.expires_at < datetime.utcnow():
            sub.status = "expired"
            await self.session.commit()
            return "free"

        return sub.plan

    # === КЛЮЧИ ===

    async def get_user_keys(self, user_id: int) -> list[TunnelKey]:
        """Получить все ключи пользователя"""
        result = await self.session.execute(
            select(TunnelKey).where(
                TunnelKey.user_id == user_id,
                TunnelKey.is_active == True
            )
        )
        return list(result.scalars().all())

    async def get_keys_count(self, user_id: int) -> int:
        """Количество активных ключей"""
        keys = await self.get_user_keys(user_id)
        return len(keys)

    async def get_next_device_num(self, user_id: int) -> int:
        """Получить следующий номер устройства"""
        result = await self.session.execute(
            select(TunnelKey).where(TunnelKey.user_id == user_id)
        )
        all_keys = list(result.scalars().all())

        if not all_keys:
            return 1

        # Извлекаем номера из marzban_username
        max_num = 0
        for key in all_keys:
            if key.marzban_username and "_d" in key.marzban_username:
                try:
                    num = int(key.marzban_username.split("_d")[-1])
                    max_num = max(max_num, num)
                except ValueError:
                    pass

        return max_num + 1

    async def can_create_key(self, user_id: int) -> tuple[bool, str, int]:
        """
        Проверить можно ли создать новый ключ.

        Returns:
            (can_create, message, max_keys)
        """
        plan = await self.get_user_plan(user_id)
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        max_keys = limits["max_keys"]

        if max_keys == 0:
            return False, "Для получения ключа нужна подписка", 0

        current_count = await self.get_keys_count(user_id)
        if current_count >= max_keys:
            return False, f"Достигнут лимит ключей ({current_count}/{max_keys})", max_keys

        return True, "OK", max_keys

    async def create_key(
        self,
        user_id: int,
        telegram_id: int,
        full_name: str = "User",
        device_name: str = "Устройство",
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Создать новый VPN ключ.

        Returns:
            (subscription_url, error)
        """
        # Проверяем лимиты
        can_create, error, max_keys = await self.can_create_key(user_id)
        if not can_create:
            return None, error

        device_num = await self.get_next_device_num(user_id)

        # Получаем expire из подписки
        subscription = await self.get_user_subscription(user_id)
        expires_at = None
        if subscription and subscription.expires_at:
            expires_at = subscription.expires_at

        if self._use_native:
            # Native режим: создаём через свой Xray
            return await self._create_key_native(
                user_id, telegram_id, device_num, device_name, expires_at
            )
        else:
            # Legacy режим: используем Marzban
            return await self._create_key_legacy(
                user_id, telegram_id, device_num, device_name, subscription
            )

    async def _create_key_native(
        self,
        user_id: int,
        telegram_id: int,
        device_num: int,
        device_name: str,
        expires_at: Optional[datetime],
    ) -> tuple[Optional[str], Optional[str]]:
        """Создать ключ через свой Xray сервис"""
        try:
            # Создаём ключ на лучшем сервере
            key, error = await self.xray.create_key_for_user(
                user_id=user_id,
                device_id=device_num,
                expires_at=expires_at,
            )

            if not key:
                return None, error or "Не удалось создать ключ"

            # Генерируем subscription URL
            sub_url = ""
            if self.config.subscription_domain:
                sub_url = self.token_generator.generate_subscription_url(
                    user_id,
                    self.config.subscription_domain
                )
            else:
                # Если домен не настроен — отдаём VLESS URL напрямую
                sub_url = key.to_vless_url()

            # Сохраняем в БД
            username = f"jarvis_{telegram_id}_d{device_num}"

            tunnel_key = TunnelKey(
                user_id=user_id,
                marzban_username=username,
                device_name=f"{device_name} {device_num}",
                subscription_url=sub_url,
                is_active=True,
            )
            self.session.add(tunnel_key)
            await self.session.commit()

            logger.info(f"VPN: создан ключ для user_id={user_id} на сервере {key.server_id}")

            return sub_url, None

        except Exception as e:
            logger.error(f"VPN: ошибка создания ключа: {e}")
            return None, str(e)

    async def _create_key_legacy(
        self,
        user_id: int,
        telegram_id: int,
        device_num: int,
        device_name: str,
        subscription: Optional[Subscription],
    ) -> tuple[Optional[str], Optional[str]]:
        """Создать ключ через Marzban (legacy)"""
        # Импортируем Marzban API
        from services.marzban_service import marzban_api, MARZBAN_URL

        plan = subscription.plan if subscription else "basic"
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["basic"])

        # Вычисляем expire_days
        if subscription and subscription.expires_at:
            days_left = (subscription.expires_at - datetime.utcnow()).days
            expire_days = max(1, days_left)
        elif subscription and subscription.expires_at is None:
            expire_days = 0  # Бессрочно
        else:
            expire_days = limits["expire_days"]

        # Создаём в Marzban
        user_data, error = await marzban_api.create_user(
            telegram_id=telegram_id,
            full_name=device_name,
            device_num=device_num,
            expire_days=expire_days,
            data_limit_gb=limits["data_limit_gb"]
        )

        if not user_data:
            return None, error or "Не удалось создать ключ"

        # Формируем subscription URL
        sub_url = user_data.get("subscription_url", "")
        if sub_url and not sub_url.startswith("http"):
            sub_url = f"{MARZBAN_URL}{sub_url}"

        # Сохраняем в БД
        username = f"tg_{telegram_id}_d{device_num}"

        tunnel_key = TunnelKey(
            user_id=user_id,
            marzban_username=username,
            device_name=f"{device_name} {device_num}",
            subscription_url=sub_url,
            is_active=True,
        )
        self.session.add(tunnel_key)
        await self.session.commit()

        return sub_url, None

    async def revoke_key(self, user_id: int, key_id: int) -> bool:
        """Отозвать ключ"""
        result = await self.session.execute(
            select(TunnelKey).where(
                TunnelKey.id == key_id,
                TunnelKey.user_id == user_id
            )
        )
        key = result.scalar_one_or_none()

        if not key:
            return False

        # Удаляем из Xray если native режим
        if self._use_native and key.marzban_username:
            # Извлекаем device_id из username
            if "_d" in key.marzban_username:
                try:
                    device_id = int(key.marzban_username.split("_d")[-1])
                    # Пробуем удалить со всех серверов
                    for server in self.config.servers:
                        await self.xray.revoke_key(user_id, device_id, server.id)
                except ValueError:
                    pass

        key.is_active = False
        await self.session.commit()
        return True

    # === СТАТИСТИКА ===

    async def get_user_stats(self, telegram_id: int) -> Optional[dict]:
        """Получить статистику пользователя"""
        if self._use_native:
            # Пока возвращаем базовую статистику
            # TODO: реализовать получение трафика из Xray
            return {
                "status": "active",
                "status_emoji": "🟢",
                "used_traffic": "N/A",
                "data_limit": "Безлимит",
                "traffic_text": "Безлимит",
                "expire_text": "Активен",
            }
        else:
            # Legacy: используем Marzban
            from services.marzban_service import marzban_api
            user_data, error = await marzban_api.get_user(telegram_id)

            if not user_data:
                return None

            status = user_data.get("status", "unknown")
            used_traffic = user_data.get("used_traffic", 0)
            data_limit = user_data.get("data_limit", 0)
            expire_ts = user_data.get("expire")

            stats = {
                "status": status,
                "status_emoji": "🟢" if status == "active" else "🔴",
                "used_traffic": bytes_to_human(used_traffic),
                "data_limit": bytes_to_human(data_limit) if data_limit else "Безлимит",
                "traffic_text": f"{bytes_to_human(used_traffic)}" + (
                    f" из {bytes_to_human(data_limit)}" if data_limit else " (Безлимит)"
                ),
            }

            if expire_ts:
                expire_date = datetime.fromtimestamp(expire_ts)
                days_left = (expire_date - datetime.now()).days
                stats["expire_date"] = expire_date.strftime("%d.%m.%Y")
                stats["days_left"] = days_left
                stats["expire_text"] = (
                    f"{stats['expire_date']} ({days_left} дн.)"
                    if days_left >= 0 else "Истекла"
                )
            else:
                stats["expire_text"] = "Бессрочно"

            return stats

    # === ПОДПИСКА ===

    async def extend_subscription(
        self,
        user_id: int,
        telegram_id: int,
        days: int,
        plan: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Продлить подписку пользователя"""
        try:
            subscription = await self.get_user_subscription(user_id)

            if subscription:
                # Продлеваем существующую
                if days > 0:
                    if subscription.expires_at and subscription.expires_at > datetime.utcnow():
                        subscription.expires_at = subscription.expires_at + timedelta(days=days)
                    else:
                        subscription.expires_at = datetime.utcnow() + timedelta(days=days)
                else:
                    subscription.expires_at = None  # Бессрочно

                if plan:
                    subscription.plan = plan
                subscription.status = "active"
            else:
                # Создаём новую
                subscription = Subscription(
                    user_id=user_id,
                    plan=plan or "basic",
                    status="active",
                    started_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(days=days) if days > 0 else None,
                )
                self.session.add(subscription)

            await self.session.commit()

            # Обновляем ключи в Xray/Marzban
            if not self._use_native:
                from services.marzban_service import marzban_api
                if days > 0:
                    await marzban_api.update_user_expire(telegram_id, days)

            return True, None

        except Exception as e:
            logger.error(f"VPN: ошибка продления подписки: {e}")
            return False, str(e)


# === SINGLETON ===

_vpn_manager: Optional[XrayManager] = None


def get_vpn_manager() -> XrayManager:
    """Получить глобальный менеджер VPN"""
    global _vpn_manager
    if _vpn_manager is None:
        _vpn_manager = XrayManager()
    return _vpn_manager


# === ОБРАТНАЯ СОВМЕСТИМОСТЬ ===
# Эти классы/функции импортируются из marzban_service
# Теперь они проксируют на VPNService

class TunnelService(VPNService):
    """
    Алиас для обратной совместимости.

    Код, использующий TunnelService из marzban_service,
    будет работать без изменений.
    """
    pass
