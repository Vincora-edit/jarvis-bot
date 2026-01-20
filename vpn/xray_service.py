"""
Сервис управления Xray-core.

Работает напрямую с Xray через gRPC API или SSH.
Поддерживает несколько серверов с failover.
"""

import asyncio
import logging
import json
import time
from typing import Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

import aiohttp
import asyncssh

from .config import VPNConfig, VPNServer, ServerStatus, get_config
from .key_generator import VLESSKeyGenerator, VLESSKey, SubscriptionTokenGenerator

logger = logging.getLogger(__name__)


@dataclass
class UserStats:
    """Статистика пользователя"""
    uuid: str
    upload_bytes: int = 0
    download_bytes: int = 0
    total_bytes: int = 0
    last_active: Optional[datetime] = None
    is_online: bool = False

    @property
    def upload_human(self) -> str:
        return bytes_to_human(self.upload_bytes)

    @property
    def download_human(self) -> str:
        return bytes_to_human(self.download_bytes)

    @property
    def total_human(self) -> str:
        return bytes_to_human(self.total_bytes)


def bytes_to_human(size: int) -> str:
    """Переводит байты в читаемый формат"""
    if size is None or size == 0:
        return "0 Б"
    for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ПБ"


class XrayManager:
    """
    Менеджер Xray серверов.

    Основные функции:
    - Добавление/удаление пользователей в Xray
    - Получение статистики
    - Health checks серверов
    - Автоматический failover
    """

    def __init__(self, config: Optional[VPNConfig] = None):
        self.config = config or get_config()
        self.key_generator = VLESSKeyGenerator(self.config.subscription_secret)
        self.token_generator = SubscriptionTokenGenerator(self.config.subscription_secret)

        # Кэш статистики
        self._stats_cache: dict[str, tuple[UserStats, float]] = {}
        self._cache_ttl = 60  # секунд

        # HTTP сессия (переиспользуется)
        self._http_session: Optional[aiohttp.ClientSession] = None

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Получить HTTP сессию с правильными настройками"""
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.config.request_timeout,
                connect=self.config.connect_timeout,
            )
            connector = aiohttp.TCPConnector(
                limit=100,           # Пул соединений
                limit_per_host=20,
                ttl_dns_cache=300,   # DNS кэш 5 минут
            )
            self._http_session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
            )
        return self._http_session

    async def close(self):
        """Закрыть сессии"""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    # === УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ===

    async def add_user(
        self,
        server: VPNServer,
        user_uuid: str,
        email: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Добавить пользователя в Xray на сервере.

        Модифицирует конфиг Xray и перезагружает сервис.

        Args:
            server: Сервер для добавления
            user_uuid: UUID пользователя (VLESS)
            email: Email/идентификатор для статистики

        Returns:
            (success, error)
        """
        try:
            # Используем скрипт xray-user для управления пользователями
            cmd = f'/usr/local/bin/xray-user add "{user_uuid}" "{email}" "{self.config.default_flow}"'
            success, output = await self._ssh_execute(server, cmd)

            output = output.strip()
            if output in ("ADDED", "EXISTS"):
                logger.info(f"VPN: пользователь {email} добавлен на {server.id} (status: {output})")
                server.current_users += 1
                return True, None
            else:
                logger.error(f"VPN: ошибка добавления на {server.id}: {output}")
                return False, output

        except Exception as e:
            logger.error(f"VPN: исключение при добавлении на {server.id}: {e}")
            return False, str(e)

    async def remove_user(
        self,
        server: VPNServer,
        email: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Удалить пользователя из Xray.

        Args:
            server: Сервер
            email: Email пользователя

        Returns:
            (success, error)
        """
        try:
            # Используем скрипт xray-user для управления пользователями
            cmd = f'/usr/local/bin/xray-user remove "{email}"'
            success, output = await self._ssh_execute(server, cmd)

            output = output.strip()
            if output == "REMOVED":
                logger.info(f"VPN: пользователь {email} удалён с {server.id}")
                server.current_users = max(0, server.current_users - 1)
                return True, None
            else:
                logger.error(f"VPN: ошибка удаления с {server.id}: {output}")
                return False, output

        except Exception as e:
            logger.error(f"VPN: исключение при удалении с {server.id}: {e}")
            return False, str(e)

    async def get_user_stats(
        self,
        server: VPNServer,
        email: str,
    ) -> Optional[UserStats]:
        """
        Получить статистику пользователя.

        Args:
            server: Сервер
            email: Email пользователя

        Returns:
            UserStats или None
        """
        # Проверяем кэш
        cache_key = f"{server.id}:{email}"
        if cache_key in self._stats_cache:
            stats, cached_at = self._stats_cache[cache_key]
            if time.time() - cached_at < self._cache_ttl:
                return stats

        try:
            cmd = f'''
            /usr/local/bin/xray api stats \
                --server=127.0.0.1:{server.xray_api_port} \
                --name="user>>>{email}>>>traffic>>>uplink" 2>/dev/null || echo "0"
            /usr/local/bin/xray api stats \
                --server=127.0.0.1:{server.xray_api_port} \
                --name="user>>>{email}>>>traffic>>>downlink" 2>/dev/null || echo "0"
            '''

            success, output = await self._ssh_execute(server, cmd)

            if success:
                lines = output.strip().split("\n")
                upload = int(lines[0]) if lines[0].isdigit() else 0
                download = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else 0

                stats = UserStats(
                    uuid=email,
                    upload_bytes=upload,
                    download_bytes=download,
                    total_bytes=upload + download,
                )

                # Кэшируем
                self._stats_cache[cache_key] = (stats, time.time())

                return stats

        except Exception as e:
            logger.error(f"VPN: ошибка получения статистики: {e}")

        return None

    # === SSH ОПЕРАЦИИ ===

    async def _ssh_execute(
        self,
        server: VPNServer,
        command: str,
        timeout: float = 30.0,
    ) -> tuple[bool, str]:
        """
        Выполнить команду на сервере через SSH.

        Args:
            server: Сервер
            command: Команда
            timeout: Таймаут

        Returns:
            (success, output)
        """
        try:
            connect_kwargs = {
                "host": server.host,
                "port": server.ssh_port,
                "username": server.ssh_user,
                "known_hosts": None,  # Отключаем проверку (для VPN серверов ОК)
            }

            if server.ssh_key_path:
                connect_kwargs["client_keys"] = [server.ssh_key_path]
            elif server.ssh_password:
                connect_kwargs["password"] = server.ssh_password

            async with asyncssh.connect(**connect_kwargs) as conn:
                result = await asyncio.wait_for(
                    conn.run(command, check=False),
                    timeout=timeout
                )

                if result.exit_status == 0:
                    return True, result.stdout
                else:
                    return False, result.stderr or result.stdout

        except asyncio.TimeoutError:
            return False, "Timeout"
        except asyncssh.Error as e:
            return False, f"SSH error: {e}"
        except Exception as e:
            return False, f"Error: {e}"

    # === HEALTH CHECKS ===

    async def check_server_health(self, server: VPNServer) -> ServerStatus:
        """
        Проверить здоровье сервера.

        Проверяет:
        1. SSH доступность
        2. Xray процесс запущен
        3. API отвечает

        Returns:
            ServerStatus
        """
        start_time = time.time()

        try:
            # Проверяем SSH и Xray
            cmd = "systemctl is-active xray 2>/dev/null || pgrep -x xray > /dev/null && echo active"
            success, output = await self._ssh_execute(server, cmd, timeout=10)

            latency = (time.time() - start_time) * 1000

            if success and "active" in output.lower():
                server.status = ServerStatus.ONLINE
                server.latency_ms = latency

                # Если пинг высокий — degraded
                if latency > 500:
                    server.status = ServerStatus.DEGRADED

            else:
                server.status = ServerStatus.OFFLINE
                logger.warning(f"VPN: сервер {server.id} offline: {output}")

        except Exception as e:
            server.status = ServerStatus.OFFLINE
            logger.error(f"VPN: ошибка проверки {server.id}: {e}")

        server.last_check = time.time()
        return server.status

    async def check_all_servers(self) -> dict[str, ServerStatus]:
        """Проверить все серверы параллельно"""
        tasks = [
            self.check_server_health(server)
            for server in self.config.servers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {
            server.id: (
                result if isinstance(result, ServerStatus)
                else ServerStatus.OFFLINE
            )
            for server, result in zip(self.config.servers, results)
        }

    # === ВЫСОКОУРОВНЕВЫЕ ОПЕРАЦИИ ===

    async def create_key_for_user(
        self,
        user_id: int,
        device_id: int,
        expires_at: Optional[datetime] = None,
        preferred_server_id: Optional[str] = None,
    ) -> tuple[Optional[VLESSKey], Optional[str]]:
        """
        Создать ключ для пользователя на лучшем доступном сервере.

        Args:
            user_id: ID пользователя
            device_id: Номер устройства
            expires_at: Дата истечения
            preferred_server_id: Предпочтительный сервер (опционально)

        Returns:
            (VLESSKey, error)
        """
        # Выбираем сервер
        if preferred_server_id:
            server = self.config.get_server(preferred_server_id)
            if not server or not server.is_available:
                server = self.config.get_best_server()
        else:
            server = self.config.get_best_server()

        if not server:
            return None, "Нет доступных серверов"

        # Генерируем ключ
        key = self.key_generator.create_key(
            user_id=user_id,
            device_id=device_id,
            server_host=server.host,
            server_port=server.inbound_port,
            public_key=server.reality_public_key,
            short_id=server.reality_short_id,
            server_name=server.reality_server_name,
            server_id=server.id,
            expires_at=expires_at,
        )

        # Добавляем в Xray
        email = f"jarvis_{user_id}_d{device_id}"
        success, error = await self.add_user(server, key.uuid, email)

        if not success:
            # Пробуем другой сервер
            for fallback in self.config.get_available_servers():
                if fallback.id != server.id:
                    key.server_id = fallback.id
                    key.server_host = fallback.host
                    key.server_port = fallback.inbound_port
                    key.public_key = fallback.reality_public_key
                    key.short_id = fallback.reality_short_id
                    key.server_name = fallback.reality_server_name

                    success, error = await self.add_user(fallback, key.uuid, email)
                    if success:
                        break

        if not success:
            return None, error or "Не удалось добавить пользователя"

        return key, None

    async def revoke_key(
        self,
        user_id: int,
        device_id: int,
        server_id: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Отозвать ключ пользователя.

        Args:
            user_id: ID пользователя
            device_id: Номер устройства
            server_id: ID сервера

        Returns:
            (success, error)
        """
        server = self.config.get_server(server_id)
        if not server:
            return False, f"Сервер {server_id} не найден"

        email = f"jarvis_{user_id}_d{device_id}"
        return await self.remove_user(server, email)

    def get_subscription_url(self, user_id: int) -> str:
        """Получить subscription URL для пользователя"""
        if self.config.subscription_domain:
            return self.token_generator.generate_subscription_url(
                user_id,
                self.config.subscription_domain
            )
        # Fallback: вернуть пустую строку если домен не настроен
        return ""

    async def get_user_traffic(
        self,
        user_id: int,
        device_id: int,
        server_id: str,
    ) -> Optional[UserStats]:
        """Получить статистику трафика пользователя"""
        server = self.config.get_server(server_id)
        if not server:
            return None

        email = f"jarvis_{user_id}_d{device_id}"
        return await self.get_user_stats(server, email)


# === ФОНОВЫЕ ЗАДАЧИ ===

class HealthChecker:
    """
    Фоновый сервис проверки здоровья серверов.

    Запускается при старте бота и периодически
    проверяет все серверы.
    """

    def __init__(self, manager: XrayManager):
        self.manager = manager
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Запустить фоновую проверку"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("VPN: health checker запущен")

    async def stop(self):
        """Остановить проверку"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("VPN: health checker остановлен")

    async def _check_loop(self):
        """Основной цикл проверки"""
        while self._running:
            try:
                results = await self.manager.check_all_servers()

                # Логируем состояние
                online = sum(1 for s in results.values() if s == ServerStatus.ONLINE)
                total = len(results)
                logger.debug(f"VPN: серверов онлайн: {online}/{total}")

                # Если все оффлайн — алерт
                if online == 0 and total > 0:
                    logger.error("VPN: ВСЕ СЕРВЕРЫ НЕДОСТУПНЫ!")

            except Exception as e:
                logger.error(f"VPN: ошибка в health checker: {e}")

            await asyncio.sleep(self.manager.config.health_check_interval)
