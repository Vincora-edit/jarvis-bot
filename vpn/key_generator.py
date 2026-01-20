"""
Генератор VLESS Reality ключей.

Создаёт конфигурации для клиентов без использования Marzban.
Формат совместим с v2rayNG, Happ, Streisand и другими клиентами.
"""

import uuid
import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


@dataclass
class VLESSKey:
    """VLESS ключ для клиента"""

    # Идентификация
    user_id: int                     # ID пользователя в БД
    device_id: int                   # Номер устройства
    uuid: str                        # UUID для VLESS

    # Сервер
    server_id: str                   # ID сервера
    server_host: str                 # IP или домен сервера
    server_port: int                 # Порт

    # Reality параметры
    public_key: str                  # Reality public key
    short_id: str                    # Short ID
    server_name: str                 # SNI (fingerprint)
    flow: str = "xtls-rprx-vision"

    # Метаданные
    name: str = ""                   # Название для клиента
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow()
        if not self.name:
            self.name = f"Jarvis VPN #{self.device_id}"

    def to_vless_url(self) -> str:
        """
        Генерирует VLESS URL для клиента.

        Формат:
        vless://uuid@host:port?type=tcp&security=reality&pbk=...&fp=chrome&sni=...&sid=...&flow=...#name
        """
        params = {
            "type": "tcp",
            "security": "reality",
            "pbk": self.public_key,
            "fp": "chrome",  # Fingerprint браузера
            "sni": self.server_name,
            "sid": self.short_id,
            "flow": self.flow,
        }

        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        name_encoded = quote(self.name)

        return f"vless://{self.uuid}@{self.server_host}:{self.server_port}?{query}#{name_encoded}"

    def to_subscription_config(self) -> dict:
        """Конфигурация для subscription JSON"""
        return {
            "v": "2",
            "ps": self.name,
            "add": self.server_host,
            "port": str(self.server_port),
            "id": self.uuid,
            "aid": "0",
            "scy": "none",
            "net": "tcp",
            "type": "none",
            "host": "",
            "path": "",
            "tls": "reality",
            "sni": self.server_name,
            "alpn": "",
            "fp": "chrome",
            "pbk": self.public_key,
            "sid": self.short_id,
            "flow": self.flow,
        }

    def to_dict(self) -> dict:
        """Полная информация о ключе"""
        return {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "uuid": self.uuid,
            "server_id": self.server_id,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "vless_url": self.to_vless_url(),
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class VLESSKeyGenerator:
    """
    Генератор VLESS ключей.

    Создаёт детерминированные UUID на основе user_id и device_id,
    чтобы ключи были воспроизводимы и не дублировались.
    """

    def __init__(self, secret: str = "jarvis-vpn-secret"):
        """
        Args:
            secret: Секрет для генерации детерминированных UUID
        """
        self.secret = secret

    def generate_uuid(self, user_id: int, device_id: int) -> str:
        """
        Генерирует детерминированный UUID для пользователя.

        UUID v5 на основе namespace + user_id + device_id.
        Один и тот же user_id + device_id всегда даёт один UUID.
        """
        # Используем HMAC для детерминированной генерации
        data = f"{user_id}:{device_id}:{self.secret}"
        digest = hashlib.sha256(data.encode()).digest()

        # Форматируем как UUID v4 (но детерминированный)
        # Устанавливаем версию 4 и вариант RFC 4122
        digest = bytearray(digest[:16])
        digest[6] = (digest[6] & 0x0f) | 0x40  # version 4
        digest[8] = (digest[8] & 0x3f) | 0x80  # variant RFC 4122

        hex_str = digest.hex()
        return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:32]}"

    def create_key(
        self,
        user_id: int,
        device_id: int,
        server_host: str,
        server_port: int,
        public_key: str,
        short_id: str,
        server_name: str = "www.google.com",
        server_id: str = "default",
        name: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> VLESSKey:
        """
        Создать VLESS ключ для пользователя.

        Args:
            user_id: ID пользователя в БД
            device_id: Номер устройства (1, 2, 3...)
            server_host: IP или домен сервера
            server_port: Порт сервера
            public_key: Reality public key
            short_id: Reality short ID
            server_name: SNI для маскировки
            server_id: ID сервера
            name: Название ключа (опционально)
            expires_at: Дата истечения (опционально)

        Returns:
            VLESSKey с готовым ключом
        """
        key_uuid = self.generate_uuid(user_id, device_id)

        return VLESSKey(
            user_id=user_id,
            device_id=device_id,
            uuid=key_uuid,
            server_id=server_id,
            server_host=server_host,
            server_port=server_port,
            public_key=public_key,
            short_id=short_id,
            server_name=server_name,
            name=name or f"Jarvis VPN #{device_id}",
            expires_at=expires_at,
        )


class SubscriptionTokenGenerator:
    """
    Генератор токенов для subscription URL.

    Токен содержит user_id и подпись, чтобы нельзя было
    подделать чужой subscription URL.
    """

    def __init__(self, secret: str):
        self.secret = secret.encode()

    def generate_token(self, user_id: int) -> str:
        """
        Генерирует токен для subscription URL.

        Формат: base64(user_id:timestamp:signature)
        """
        timestamp = int(datetime.utcnow().timestamp())
        data = f"{user_id}:{timestamp}"

        # HMAC подпись
        signature = hmac.new(
            self.secret,
            data.encode(),
            hashlib.sha256
        ).hexdigest()[:16]

        # Кодируем в base64 для URL
        token_data = f"{data}:{signature}"
        token = base64.urlsafe_b64encode(token_data.encode()).decode().rstrip("=")

        return token

    def verify_token(self, token: str) -> Optional[int]:
        """
        Проверяет токен и возвращает user_id.

        Returns:
            user_id если токен валидный, None если нет
        """
        try:
            # Восстанавливаем padding
            padding = 4 - len(token) % 4
            if padding != 4:
                token += "=" * padding

            token_data = base64.urlsafe_b64decode(token.encode()).decode()
            parts = token_data.split(":")

            if len(parts) != 3:
                return None

            user_id = int(parts[0])
            timestamp = int(parts[1])
            signature = parts[2]

            # Проверяем подпись
            data = f"{user_id}:{timestamp}"
            expected_sig = hmac.new(
                self.secret,
                data.encode(),
                hashlib.sha256
            ).hexdigest()[:16]

            if not hmac.compare_digest(signature, expected_sig):
                logger.warning(f"VPN: неверная подпись токена для user_id={user_id}")
                return None

            # Токен валиден (можно добавить проверку timestamp если нужно)
            return user_id

        except Exception as e:
            logger.warning(f"VPN: ошибка проверки токена: {e}")
            return None

    def generate_subscription_url(self, user_id: int, domain: str) -> str:
        """
        Генерирует полный subscription URL.

        Args:
            user_id: ID пользователя
            domain: Домен subscription сервера

        Returns:
            URL вида https://domain/sub/token
        """
        token = self.generate_token(user_id)
        return f"https://{domain}/sub/{token}"
