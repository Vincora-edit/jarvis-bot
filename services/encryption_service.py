"""
Сервис шифрования данных.
Использует Fernet (AES-128-CBC) для защиты чувствительных данных в БД.
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import config


class EncryptionService:
    """Сервис для шифрования/дешифрования данных"""

    _instance = None
    _fernet = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_cipher()
        return cls._instance

    def _init_cipher(self):
        """Инициализация шифра из ключа в конфиге"""
        encryption_key = getattr(config, 'ENCRYPTION_KEY', None)

        if not encryption_key:
            # Если ключа нет — генерируем предупреждение
            print("⚠️ ENCRYPTION_KEY не установлен! Шифрование отключено.")
            print("   Добавьте в .env: ENCRYPTION_KEY=<ваш_ключ>")
            print("   Сгенерировать: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
            self._fernet = None
            return

        try:
            # Проверяем, валидный ли ключ Fernet
            self._fernet = Fernet(encryption_key.encode())
        except Exception as e:
            print(f"⚠️ Ошибка инициализации шифрования: {e}")
            self._fernet = None

    @property
    def is_enabled(self) -> bool:
        """Проверить, включено ли шифрование"""
        return self._fernet is not None

    def encrypt(self, data: str) -> str:
        """
        Зашифровать строку.
        Возвращает base64-encoded зашифрованные данные.
        Если шифрование отключено — возвращает исходные данные.
        """
        if not self._fernet or not data:
            return data

        try:
            encrypted = self._fernet.encrypt(data.encode('utf-8'))
            return base64.urlsafe_b64encode(encrypted).decode('utf-8')
        except Exception as e:
            print(f"⚠️ Ошибка шифрования: {e}")
            return data

    def decrypt(self, encrypted_data: str) -> str:
        """
        Расшифровать строку.
        Если шифрование отключено или данные не зашифрованы — возвращает как есть.
        """
        if not self._fernet or not encrypted_data:
            return encrypted_data

        try:
            # Пробуем расшифровать
            decoded = base64.urlsafe_b64decode(encrypted_data.encode('utf-8'))
            decrypted = self._fernet.decrypt(decoded)
            return decrypted.decode('utf-8')
        except Exception:
            # Если не получилось — возможно данные не зашифрованы
            return encrypted_data

    def encrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """Зашифровать указанные поля в словаре"""
        if not self._fernet:
            return data

        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.encrypt(str(result[field]))
        return result

    def decrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """Расшифровать указанные поля в словаре"""
        if not self._fernet:
            return data

        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.decrypt(str(result[field]))
        return result


# Глобальный экземпляр
encryption = EncryptionService()


def generate_key() -> str:
    """Сгенерировать новый ключ шифрования"""
    return Fernet.generate_key().decode()
