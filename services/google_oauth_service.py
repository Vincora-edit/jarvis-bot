"""
Сервис OAuth авторизации для Google Calendar.
Позволяет каждому пользователю подключить свой календарь.
"""
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

from config import config


# Хранилище временных state для OAuth (telegram_id -> state)
_oauth_states: dict[str, int] = {}


class GoogleOAuthService:
    """Сервис для OAuth авторизации Google Calendar"""

    SCOPES = [
        'https://www.googleapis.com/auth/calendar.readonly',
        'https://www.googleapis.com/auth/calendar.events',
        'https://www.googleapis.com/auth/calendar'
    ]

    def __init__(self):
        self.client_id = config.GOOGLE_CLIENT_ID
        self.client_secret = config.GOOGLE_CLIENT_SECRET
        self.redirect_uri = config.GOOGLE_REDIRECT_URI

    def create_auth_url(self, telegram_id: int) -> str:
        """Создать URL для авторизации пользователя"""
        # Генерируем уникальный state
        state = secrets.token_urlsafe(32)
        _oauth_states[state] = telegram_id
        print(f"🔐 OAuth state created: {state[:20]}... for user {telegram_id}")
        print(f"   Total states in memory: {len(_oauth_states)}")

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri],
                }
            },
            scopes=self.SCOPES,
            redirect_uri=self.redirect_uri
        )

        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            state=state,
            prompt='consent'  # Всегда показывать экран выбора аккаунта
        )

        return auth_url

    def exchange_code(self, code: str, state: str) -> tuple[Optional[dict], Optional[int]]:
        """
        Обменять код авторизации на токены.
        Возвращает (credentials_dict, telegram_id) или (None, None) при ошибке.
        """
        # Проверяем state
        print(f"🔍 OAuth exchange_code called")
        print(f"   State received: {state}")
        print(f"   States in memory: {list(_oauth_states.keys())}")

        telegram_id = _oauth_states.pop(state, None)
        if telegram_id is None:
            print(f"   ❌ State NOT found in memory!")
            return None, None

        print(f"   ✅ State found! telegram_id={telegram_id}")

        try:
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": [self.redirect_uri],
                    }
                },
                scopes=self.SCOPES,
                redirect_uri=self.redirect_uri
            )

            flow.fetch_token(code=code)
            credentials = flow.credentials

            # Сохраняем как словарь
            creds_dict = {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': list(credentials.scopes),
                'expiry': credentials.expiry.isoformat() if credentials.expiry else None
            }

            return creds_dict, telegram_id

        except Exception as e:
            print(f"OAuth error: {e}")
            return None, None

    @staticmethod
    def credentials_from_dict(creds_dict: dict) -> Optional[Credentials]:
        """Восстановить Credentials из словаря"""
        if not creds_dict:
            return None

        try:
            expiry = None
            if creds_dict.get('expiry'):
                expiry = datetime.fromisoformat(creds_dict['expiry'])

            credentials = Credentials(
                token=creds_dict['token'],
                refresh_token=creds_dict.get('refresh_token'),
                token_uri=creds_dict.get('token_uri', 'https://oauth2.googleapis.com/token'),
                client_id=creds_dict.get('client_id'),
                client_secret=creds_dict.get('client_secret'),
                scopes=creds_dict.get('scopes'),
                expiry=expiry
            )

            # Обновляем токен если истёк
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())

            return credentials

        except Exception as e:
            print(f"Error restoring credentials: {e}")
            return None

    @staticmethod
    def credentials_to_dict(credentials: Credentials) -> dict:
        """Преобразовать Credentials в словарь для хранения"""
        return {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': list(credentials.scopes) if credentials.scopes else [],
            'expiry': credentials.expiry.isoformat() if credentials.expiry else None
        }

    @staticmethod
    def get_telegram_id_by_state(state: str) -> Optional[int]:
        """Получить telegram_id по state (не удаляя)"""
        return _oauth_states.get(state)

    @staticmethod
    def cleanup_expired_states():
        """Очистить старые states (вызывать периодически)"""
        # В простой реализации просто чистим если слишком много
        if len(_oauth_states) > 1000:
            _oauth_states.clear()
