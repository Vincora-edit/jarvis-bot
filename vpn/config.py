"""
Конфигурация VPN серверов.

Поддержка нескольких серверов с автоматическим failover.
Все credentials берутся из переменных окружения.
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ServerStatus(Enum):
    """Статус сервера"""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"  # Работает, но медленно
    UNKNOWN = "unknown"


@dataclass
class VPNServer:
    """Конфигурация одного VPN сервера"""

    # Идентификация
    id: str                          # Уникальный ID (eu1, us1, etc)
    name: str                        # Название для пользователя
    location: str                    # Локация (Европа, США, etc)

    # Подключение к серверу (SSH/API)
    host: str                        # IP или домен
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key_path: Optional[str] = None
    ssh_password: Optional[str] = None

    # Xray конфигурация
    xray_api_port: int = 10085       # Xray gRPC API порт
    inbound_port: int = 443          # Порт для клиентов
    inbound_tag: str = "vless-reality"

    # Reality настройки
    reality_private_key: str = ""    # x25519 приватный ключ
    reality_public_key: str = ""     # x25519 публичный ключ (для клиентов)
    reality_short_id: str = ""       # Short ID
    reality_server_name: str = "www.google.com"  # SNI для маскировки

    # Статус и метрики
    status: ServerStatus = ServerStatus.UNKNOWN
    latency_ms: Optional[float] = None
    last_check: Optional[float] = None
    priority: int = 10               # Меньше = выше приоритет

    # Лимиты
    max_users: int = 1000
    current_users: int = 0

    def __post_init__(self):
        """Валидация после создания"""
        if not self.host:
            raise ValueError(f"Server {self.id}: host is required")

    @property
    def is_available(self) -> bool:
        """Сервер доступен для новых подключений"""
        # UNKNOWN считается доступным пока не проверен health check
        return (
            self.status in (ServerStatus.ONLINE, ServerStatus.UNKNOWN) and
            self.current_users < self.max_users and
            bool(self.reality_public_key)  # Должен быть настроен
        )

    @property
    def xray_api_address(self) -> str:
        """Адрес для Xray gRPC API"""
        return f"{self.host}:{self.xray_api_port}"

    def to_dict(self) -> dict:
        """Сериализация для API/логов"""
        return {
            "id": self.id,
            "name": self.name,
            "location": self.location,
            "host": self.host,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "priority": self.priority,
            "is_available": self.is_available,
        }


@dataclass
class VPNConfig:
    """Глобальная конфигурация VPN сервиса"""

    # Серверы
    servers: list[VPNServer] = field(default_factory=list)

    # Домен для subscription URL
    subscription_domain: str = ""    # vpn.jarvis.bot
    subscription_secret: str = ""    # Секрет для подписи токенов

    # Настройки по умолчанию
    default_flow: str = "xtls-rprx-vision"
    default_network: str = "tcp"
    default_security: str = "reality"

    # Таймауты (в секундах)
    connect_timeout: float = 10.0
    request_timeout: float = 30.0
    health_check_interval: float = 60.0

    # Retry настройки
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0       # Экспоненциальный backoff

    # Лимиты по планам (переопределяет marzban_service)
    plan_limits: dict = field(default_factory=lambda: {
        "free": {"max_keys": 0, "expire_days": 0},
        "free_trial": {"max_keys": 1, "expire_days": 14},
        "basic": {"max_keys": 3, "expire_days": 30},
        "standard": {"max_keys": 5, "expire_days": 30},
        "pro": {"max_keys": 10, "expire_days": 30},
    })

    def get_available_servers(self) -> list[VPNServer]:
        """Получить доступные серверы, отсортированные по приоритету"""
        available = [s for s in self.servers if s.is_available]
        return sorted(available, key=lambda s: (s.priority, s.latency_ms or 9999))

    def get_server(self, server_id: str) -> Optional[VPNServer]:
        """Получить сервер по ID"""
        for server in self.servers:
            if server.id == server_id:
                return server
        return None

    def get_best_server(self) -> Optional[VPNServer]:
        """Получить лучший доступный сервер"""
        available = self.get_available_servers()
        return available[0] if available else None


def get_vpn_config() -> VPNConfig:
    """
    Загрузить конфигурацию VPN из переменных окружения.

    Переменные:
    - VPN_SUBSCRIPTION_DOMAIN: домен для subscription URL
    - VPN_SUBSCRIPTION_SECRET: секрет для токенов
    - VPN_SERVERS: JSON с конфигурацией серверов

    Пример VPN_SERVERS:
    [
        {
            "id": "eu1",
            "name": "Европа 1",
            "location": "Германия",
            "host": "72.56.88.242",
            "reality_private_key": "...",
            "reality_public_key": "...",
            "reality_short_id": "abc123"
        }
    ]
    """
    config = VPNConfig(
        subscription_domain=os.getenv("VPN_SUBSCRIPTION_DOMAIN", ""),
        subscription_secret=os.getenv("VPN_SUBSCRIPTION_SECRET", "jarvis-vpn-secret-change-me"),
    )

    # Загружаем серверы из JSON
    servers_json = os.getenv("VPN_SERVERS", "")
    if servers_json:
        try:
            servers_data = json.loads(servers_json)
            for srv in servers_data:
                server = VPNServer(
                    id=srv.get("id", "default"),
                    name=srv.get("name", "VPN Server"),
                    location=srv.get("location", "Unknown"),
                    host=srv.get("host", ""),
                    ssh_port=srv.get("ssh_port", 22),
                    ssh_user=srv.get("ssh_user", "root"),
                    ssh_password=srv.get("ssh_password"),
                    ssh_key_path=srv.get("ssh_key_path"),
                    xray_api_port=srv.get("xray_api_port", 10085),
                    inbound_port=srv.get("inbound_port", 443),
                    inbound_tag=srv.get("inbound_tag", "vless-reality"),
                    reality_private_key=srv.get("reality_private_key", ""),
                    reality_public_key=srv.get("reality_public_key", ""),
                    reality_short_id=srv.get("reality_short_id", ""),
                    reality_server_name=srv.get("reality_server_name", "www.google.com"),
                    priority=srv.get("priority", 10),
                    max_users=srv.get("max_users", 1000),
                )
                config.servers.append(server)
                logger.info(f"VPN: загружен сервер {server.id} ({server.host})")
        except json.JSONDecodeError as e:
            logger.error(f"VPN: ошибка парсинга VPN_SERVERS: {e}")

    # Если нет серверов из env, добавляем текущий Marzban как fallback
    if not config.servers:
        logger.warning("VPN: нет серверов в VPN_SERVERS, используем fallback")
        # Берём данные из старых переменных для совместимости
        fallback_host = os.getenv("MARZBAN_HOST", "72.56.88.242")
        if fallback_host:
            config.servers.append(VPNServer(
                id="legacy",
                name="Основной сервер",
                location="Европа",
                host=fallback_host,
                priority=100,  # Низкий приоритет
            ))

    return config


# Глобальный экземпляр конфигурации (ленивая инициализация)
_config: Optional[VPNConfig] = None


def get_config() -> VPNConfig:
    """Получить глобальную конфигурацию (singleton)"""
    global _config
    if _config is None:
        _config = get_vpn_config()
    return _config
