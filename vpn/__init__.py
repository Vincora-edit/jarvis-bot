"""
Jarvis VPN Service - собственный VPN сервис без Marzban.

Компоненты:
- XrayManager: управление Xray-core конфигурацией
- KeyGenerator: генерация VLESS Reality ключей
- SubscriptionAPI: endpoint для клиентов
- HealthChecker: мониторинг серверов
"""

from .config import VPNConfig, get_vpn_config
from .xray_service import XrayManager
from .key_generator import VLESSKeyGenerator

__all__ = [
    "VPNConfig",
    "get_vpn_config",
    "XrayManager",
    "VLESSKeyGenerator",
]
