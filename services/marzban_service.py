"""
Сервис для работы с Marzban VPN API.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TunnelKey, Subscription

logger = logging.getLogger(__name__)


# === КОНФИГУРАЦИЯ MARZBAN ===
MARZBAN_URL = "https://72.56.88.242:8000"
MARZBAN_ADMIN_USERNAME = "Nfjk3khj43h043gj3\u201343"  # Unicode en-dash
MARZBAN_ADMIN_PASSWORD = "Vincorafjk3n4-423"

# Лимиты VPN по планам
# max_keys = максимальное количество ключей (устройств) у пользователя
VPN_PLAN_LIMITS = {
    "free": {"max_keys": 0, "data_limit_gb": 0, "expire_days": 0},       # Нет VPN (только триал)
    "free_trial": {"max_keys": 1, "data_limit_gb": 0, "expire_days": 7}, # Триал 1 устройство
    "basic": {"max_keys": 3, "data_limit_gb": 0, "expire_days": 30},     # 3 устройства
    "standard": {"max_keys": 5, "data_limit_gb": 0, "expire_days": 30},  # 5 устройств
    "pro": {"max_keys": 10, "data_limit_gb": 0, "expire_days": 30},      # 10 устройств
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


class MarzbanAPI:
    """Клиент для Marzban API"""

    def __init__(self):
        self.base_url = MARZBAN_URL
        self.username = MARZBAN_ADMIN_USERNAME
        self.password = MARZBAN_ADMIN_PASSWORD
        self.token: Optional[str] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Создать HTTP сессию с отключенной проверкой SSL"""
        connector = aiohttp.TCPConnector(ssl=False)
        return aiohttp.ClientSession(connector=connector)

    async def _auth(self) -> tuple[bool, Optional[str]]:
        """Авторизация и получение токена"""
        async with await self._get_session() as session:
            url = f"{self.base_url}/api/admin/token"
            data = {"username": self.username, "password": self.password}

            try:
                async with session.post(url, data=data) as response:
                    if response.status == 200:
                        json_resp = await response.json()
                        self.token = json_resp.get("access_token")
                        logger.info("Marzban: авторизация успешна")
                        return True, None
                    else:
                        text = await response.text()
                        logger.error(f"Marzban auth error: {response.status} - {text}")
                        return False, f"HTTP {response.status}: {text}"
            except Exception as e:
                logger.error(f"Marzban connection error: {e}")
                return False, f"Ошибка соединения: {e}"

    async def _ensure_auth(self) -> bool:
        """Убедиться что токен есть"""
        if not self.token:
            success, _ = await self._auth()
            return success
        return True

    async def create_user(
        self,
        telegram_id: int,
        full_name: str,
        device_num: int = 1,
        expire_days: int = 30,
        data_limit_gb: int = 0
    ) -> tuple[Optional[dict], Optional[str]]:
        """
        Создать пользователя в Marzban.
        device_num - номер устройства (1, 2, 3...)
        Возвращает (user_data, error)
        """
        if not await self._ensure_auth():
            return None, "Не удалось авторизоваться в Marzban"

        # Уникальный username для каждого устройства
        username = f"tg_{telegram_id}_d{device_num}"

        expire_timestamp = None
        if expire_days > 0:
            expire_timestamp = int((datetime.utcnow() + timedelta(days=expire_days)).timestamp())

        payload = {
            "username": username,
            "proxies": {"vless": {"flow": "xtls-rprx-vision"}},
            "inbounds": {"vless": ["VLESS TCP REALITY"]},
            "expire": expire_timestamp,
            "data_limit": data_limit_gb * 1024 * 1024 * 1024 if data_limit_gb > 0 else 0,
            "status": "active",
            "note": f"Telegram: {full_name} (ID: {telegram_id}) Device #{device_num}"
        }

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        async with await self._get_session() as session:
            # Проверяем существование
            async with session.get(
                f"{self.base_url}/api/user/{username}",
                headers=headers
            ) as response:
                if response.status == 200:
                    # Пользователь уже есть — обновляем expire и активируем
                    existing_user = await response.json()
                    logger.info(f"Marzban: пользователь {username} уже существует, обновляем")

                    update_payload = {
                        "expire": expire_timestamp,
                        "status": "active",
                    }

                    async with session.put(
                        f"{self.base_url}/api/user/{username}",
                        json=update_payload,
                        headers=headers
                    ) as update_resp:
                        if update_resp.status == 200:
                            user_data = await update_resp.json()
                            logger.info(f"Marzban: пользователь {username} обновлён")
                            return user_data, None
                        else:
                            logger.warning(f"Marzban: не удалось обновить {username}, возвращаем текущего")
                            return existing_user, None

            # Создаём нового
            async with session.post(
                f"{self.base_url}/api/user",
                json=payload,
                headers=headers
            ) as response:
                if response.status == 200:
                    user_data = await response.json()
                    logger.info(f"Marzban: создан пользователь {username}")
                    return user_data, None
                elif response.status == 401:
                    # Токен истёк — перелогиниваемся
                    success, _ = await self._auth()
                    if success:
                        headers["Authorization"] = f"Bearer {self.token}"
                        async with session.post(
                            f"{self.base_url}/api/user",
                            json=payload,
                            headers=headers
                        ) as retry_resp:
                            if retry_resp.status == 200:
                                return await retry_resp.json(), None
                            text = await retry_resp.text()
                            return None, f"Ошибка: {retry_resp.status}"
                    return None, "Не удалось обновить токен"
                else:
                    text = await response.text()
                    logger.error(f"Marzban create user error: {response.status} - {text}")
                    return None, f"Ошибка сервера: {response.status}"

    async def get_user(self, telegram_id: int) -> tuple[Optional[dict], Optional[str]]:
        """Получить данные пользователя"""
        if not await self._ensure_auth():
            return None, "Не удалось авторизоваться"

        username = f"tg_user_{telegram_id}"
        headers = {"Authorization": f"Bearer {self.token}"}

        async with await self._get_session() as session:
            async with session.get(
                f"{self.base_url}/api/user/{username}",
                headers=headers
            ) as response:
                if response.status == 200:
                    return await response.json(), None
                elif response.status == 404:
                    return None, "not_found"
                else:
                    return None, f"Ошибка: {response.status}"

    async def update_user_expire(
        self,
        telegram_id: int,
        expire_days: int
    ) -> tuple[bool, Optional[str]]:
        """Продлить подписку пользователя"""
        if not await self._ensure_auth():
            return False, "Не удалось авторизоваться"

        username = f"tg_user_{telegram_id}"

        # Получаем текущие данные
        user_data, error = await self.get_user(telegram_id)
        if not user_data:
            return False, error

        # Вычисляем новую дату
        current_expire = user_data.get("expire")
        if current_expire and current_expire > datetime.utcnow().timestamp():
            # Продлеваем от текущей даты истечения
            new_expire = int(current_expire + expire_days * 24 * 3600)
        else:
            # Начинаем с сегодня
            new_expire = int((datetime.utcnow() + timedelta(days=expire_days)).timestamp())

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        async with await self._get_session() as session:
            async with session.put(
                f"{self.base_url}/api/user/{username}",
                json={"expire": new_expire, "status": "active"},
                headers=headers
            ) as response:
                if response.status == 200:
                    logger.info(f"Marzban: продлена подписка {username} на {expire_days} дней")
                    return True, None
                else:
                    text = await response.text()
                    return False, f"Ошибка: {response.status}"

    async def disable_user(self, telegram_id: int) -> tuple[bool, Optional[str]]:
        """Отключить пользователя"""
        if not await self._ensure_auth():
            return False, "Не удалось авторизоваться"

        username = f"tg_user_{telegram_id}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        async with await self._get_session() as session:
            async with session.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": "disabled"},
                headers=headers
            ) as response:
                if response.status == 200:
                    logger.info(f"Marzban: отключен пользователь {username}")
                    return True, None
                else:
                    return False, f"Ошибка: {response.status}"

    async def update_user_ip_limit(
        self,
        telegram_id: int,
        ip_limit: int
    ) -> tuple[bool, Optional[str]]:
        """Обновить лимит одновременных подключений (при смене плана)"""
        if not await self._ensure_auth():
            return False, "Не удалось авторизоваться"

        username = f"tg_user_{telegram_id}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        async with await self._get_session() as session:
            async with session.put(
                f"{self.base_url}/api/user/{username}",
                json={"ip_limit": ip_limit},
                headers=headers
            ) as response:
                if response.status == 200:
                    logger.info(f"Marzban: обновлен ip_limit для {username} = {ip_limit}")
                    return True, None
                else:
                    text = await response.text()
                    return False, f"Ошибка: {response.status}"


# Глобальный экземпляр API
marzban_api = MarzbanAPI()


class TunnelService:
    """Сервис для управления VPN туннелями"""

    def __init__(self, session: AsyncSession):
        self.session = session

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
        """Получить следующий номер устройства (учитывая все ключи, включая удалённые)"""
        result = await self.session.execute(
            select(TunnelKey).where(TunnelKey.user_id == user_id)
        )
        all_keys = list(result.scalars().all())

        if not all_keys:
            return 1

        # Извлекаем номера из marzban_username (формат: tg_{telegram_id}_d{num})
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
        Возвращает (can_create, message, max_keys)
        """
        plan = await self.get_user_plan(user_id)
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        max_keys = limits["max_keys"]

        if max_keys == 0:
            return False, "Для получения ключа нужна подписка", 0

        current_count = await self.get_keys_count(user_id)
        if current_count >= max_keys:
            return False, f"Достигнут лимит ключей ({current_count}/{max_keys}). Удалите старый ключ или улучшите план.", max_keys

        return True, "OK", max_keys

    async def create_key(
        self,
        user_id: int,
        telegram_id: int,
        full_name: str,
        device_name: str = "Устройство"
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Создать новый ключ для нового устройства.
        Возвращает (subscription_url, error)
        """
        # Проверяем лимиты
        can_create, error, max_keys = await self.can_create_key(user_id)
        if not can_create:
            return None, error

        # Получаем следующий номер устройства (учитывая все ключи, включая удалённые)
        device_num = await self.get_next_device_num(user_id)

        # Получаем план и подписку для настроек
        plan = await self.get_user_plan(user_id)
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["basic"])

        # Вычисляем expire_days из реальной подписки
        subscription = await self.get_user_subscription(user_id)
        if subscription and subscription.expires_at:
            days_left = (subscription.expires_at - datetime.utcnow()).days
            expire_days = max(1, days_left)
        elif subscription and subscription.expires_at is None:
            expire_days = 0
        else:
            expire_days = limits["expire_days"]

        # Создаём пользователя в Marzban с уникальным username для устройства
        user_data, error = await marzban_api.create_user(
            telegram_id=telegram_id,
            full_name=full_name,
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
        marzban_username = f"tg_{telegram_id}_d{device_num}"

        key = TunnelKey(
            user_id=user_id,
            marzban_username=marzban_username,
            device_name=f"{device_name} {device_num}",
            subscription_url=sub_url,
            is_active=True
        )
        self.session.add(key)
        await self.session.commit()

        return sub_url, None

    async def get_user_stats(self, telegram_id: int) -> Optional[dict]:
        """Получить статистику пользователя из Marzban"""
        user_data, error = await marzban_api.get_user(telegram_id)

        if not user_data:
            return None

        # Форматируем данные
        status = user_data.get("status", "unknown")
        used_traffic = user_data.get("used_traffic", 0)
        data_limit = user_data.get("data_limit", 0)
        expire_ts = user_data.get("expire")

        stats = {
            "status": status,
            "status_emoji": "🟢" if status == "active" else "🔴",
            "used_traffic": bytes_to_human(used_traffic),
            "data_limit": bytes_to_human(data_limit) if data_limit else "Безлимит",
            "traffic_text": f"{bytes_to_human(used_traffic)}" + (f" из {bytes_to_human(data_limit)}" if data_limit else " (Безлимит)"),
        }

        if expire_ts:
            expire_date = datetime.fromtimestamp(expire_ts)
            days_left = (expire_date - datetime.now()).days
            stats["expire_date"] = expire_date.strftime("%d.%m.%Y")
            stats["days_left"] = days_left
            stats["expire_text"] = f"{stats['expire_date']} ({days_left} дн.)" if days_left >= 0 else "Истекла"
        else:
            stats["expire_text"] = "Бессрочно"

        return stats

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

        key.is_active = False
        await self.session.commit()
        return True

    async def extend_subscription(
        self,
        user_id: int,
        telegram_id: int,
        days: int,
        plan: str = None
    ) -> tuple[bool, Optional[str]]:
        """
        Продлить подписку пользователя.
        Обновляет и БД бота, и Marzban.

        Args:
            user_id: ID пользователя в БД бота
            telegram_id: Telegram ID
            days: Количество дней для продления (0 = бессрочно)
            plan: Новый план (если меняется)

        Returns:
            (success, error)
        """
        try:
            # 1. Получаем текущую подписку
            subscription = await self.get_user_subscription(user_id)

            if subscription:
                # Продлеваем существующую
                if days > 0:
                    if subscription.expires_at and subscription.expires_at > datetime.utcnow():
                        # Продлеваем от текущей даты окончания
                        subscription.expires_at = subscription.expires_at + timedelta(days=days)
                    else:
                        # Начинаем с сегодня
                        subscription.expires_at = datetime.utcnow() + timedelta(days=days)
                else:
                    # Бессрочно
                    subscription.expires_at = None

                if plan:
                    subscription.plan = plan

                subscription.status = "active"
            else:
                # Создаём новую подписку
                subscription = Subscription(
                    user_id=user_id,
                    plan=plan or "basic",
                    status="active",
                    started_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(days=days) if days > 0 else None
                )
                self.session.add(subscription)

            await self.session.commit()

            # 2. Обновляем в Marzban
            if days > 0:
                success, error = await marzban_api.update_user_expire(telegram_id, days)
            else:
                # Для бессрочной нужно установить expire=None
                # Это делается через общий update_user
                success = True  # Пока пропускаем

            if not success:
                logger.error(f"Не удалось обновить expire в Marzban: {error}")
                # Не откатываем БД — подписка всё равно продлена

            # ip_limit не поддерживается Marzban API, используем отдельные ключи для устройств

            return True, None

        except Exception as e:
            logger.error(f"Ошибка продления подписки: {e}")
            return False, str(e)

    async def activate_promo_subscription(
        self,
        user_id: int,
        telegram_id: int,
        plan: str,
        days: int
    ) -> tuple[bool, Optional[str]]:
        """
        Активировать подписку по промокоду.
        Создаёт подписку в БД и настраивает Marzban.

        Args:
            user_id: ID пользователя в БД бота
            telegram_id: Telegram ID
            plan: План подписки
            days: Дней подписки (0 = бессрочно)

        Returns:
            (success, error)
        """
        return await self.extend_subscription(user_id, telegram_id, days, plan)
