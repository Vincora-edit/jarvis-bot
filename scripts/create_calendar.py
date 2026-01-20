import os.path
import pickle
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ВАЖНО: Измените scope на полный доступ
SCOPES = ['https://www.googleapis.com/auth/calendar']

CREDENTIALS_FILE = 'client_secret.json'
REDIRECTED_URI = 'http://localhost:8080'


def get_calendar_service():
    creds = None
    
    # Проверяем сохраненные токены
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # Если нет валидных токенов
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, 
                SCOPES,
                redirect_uri=REDIRECTED_URI
            )
            # Используем run_local_server с автоматическим портом
            creds = flow.run_local_server(port=0)
        
        # Сохраняем токены
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    # Создаем сервис БЕЗ API ключа (только OAuth)
    try:
        service = build('calendar', 'v3', credentials=creds)
        print("✅ Google Calendar сервис создан успешно")
        return service
    except Exception as e:
        print(f"❌ Ошибка создания сервиса: {e}")
        raise