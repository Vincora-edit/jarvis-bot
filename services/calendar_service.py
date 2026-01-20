"""
Сервис для работы с Google Calendar.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz

from googleapiclient.discovery import build
from scripts.create_calendar import get_calendar_service
from config import config

logger = logging.getLogger(__name__)


class CalendarService:
    """Работа с Google Calendar API"""

    # Словарь ключевых слов для подбора эмодзи
    EMOJI_KEYWORDS = {
        # Встречи и созвоны
        "созвон": "📞", "звонок": "📞", "call": "📞", "колл": "📞",
        "встреча": "🤝", "meeting": "🤝", "митинг": "🤝",
        "zoom": "💻", "зум": "💻", "teams": "💻", "скайп": "💻", "skype": "💻",
        "планерка": "📋", "стендап": "🎯", "синк": "🔄", "дейли": "🌅",

        # Работа
        "работа": "💼", "work": "💼", "офис": "🏢", "office": "🏢",
        "дедлайн": "⏰", "deadline": "⏰", "срок": "⏰",
        "презентация": "📊", "presentation": "📊",
        "собеседование": "👔", "интервью": "👔", "interview": "👔",

        # Здоровье и спорт
        "врач": "👨‍⚕️", "доктор": "👨‍⚕️", "больница": "🏥", "клиника": "🏥",
        "терапия": "💆", "массаж": "💆", "такар": "💆",
        "стоматолог": "🦷", "зубной": "🦷", "dentist": "🦷",
        "барбершоп": "💈", "барбер": "💈", "парикмахер": "💇", "стрижка": "💇",
        "спорт": "🏃", "тренировка": "💪", "gym": "💪", "фитнес": "💪", "зал": "💪",
        "йога": "🧘", "yoga": "🧘", "медитация": "🧘",
        "бег": "🏃", "пробежка": "🏃", "run": "🏃",

        # Еда
        "обед": "🍽", "lunch": "🍽", "ужин": "🍽", "dinner": "🍽",
        "завтрак": "☕", "breakfast": "☕", "кофе": "☕", "coffee": "☕",
        "ресторан": "🍴", "кафе": "☕",

        # Учёба
        "учёба": "📚", "учеба": "📚", "урок": "📖", "lesson": "📖",
        "курс": "🎓", "лекция": "🎓", "вебинар": "🎓", "webinar": "🎓",
        "экзамен": "📝", "тест": "📝", "exam": "📝",

        # Дети и семья
        "ребенок": "👶", "ребёнок": "👶", "дети": "👨‍👩‍👧", "детей": "👨‍👩‍👧",
        "садик": "💒", "детский сад": "💒", "школа": "🎒", "школу": "🎒",
        "забрать": "🚗", "отвезти": "🚗", "привезти": "🚗",
        "сын": "👦", "дочь": "👧", "дочка": "👧", "сына": "👦", "дочку": "👧",

        # Личное
        "день рождения": "🎂", "др": "🎂", "birthday": "🎂",
        "праздник": "🎉", "party": "🎉", "вечеринка": "🎉",
        "отпуск": "🏖", "vacation": "🏖", "отдых": "🏖",
        "путешествие": "✈️", "поездка": "🚗", "trip": "✈️",
        "покупки": "🛒", "shopping": "🛒", "магазин": "🛒",

        # Разное
        "напоминание": "🔔", "reminder": "🔔",
        "задача": "✅", "task": "✅", "todo": "✅",
        "проект": "📋", "project": "📋",
        "идея": "💡", "idea": "💡",
        "ритуал": "🌟", "утренний": "🌅", "вечерний": "🌙",
        "купить": "🛒", "планшет": "📱", "телефон": "📱", "техника": "💻",
        "галера": "⛵", "галере": "⛵",
    }

    def __init__(self, user_credentials: dict = None):
        """
        Инициализация сервиса.
        user_credentials — токены пользователя из БД (если подключен свой календарь)
        """
        if user_credentials:
            # Используем токены пользователя
            from services.google_oauth_service import GoogleOAuthService
            credentials = GoogleOAuthService.credentials_from_dict(user_credentials)
            if credentials:
                self.service = build('calendar', 'v3', credentials=credentials)
            else:
                # Фоллбэк на общий календарь
                self.service = get_calendar_service()
        else:
            # Используем общий service account
            self.service = get_calendar_service()

        self.timezone = pytz.timezone(config.TIMEZONE)
        self._calendars_cache = None  # Кэш списка календарей

    def get_all_calendars(self) -> list[dict]:
        """Получить список всех доступных календарей пользователя"""
        if self._calendars_cache is not None:
            return self._calendars_cache

        try:
            calendar_list = self.service.calendarList().list().execute()
            calendars = []
            for cal in calendar_list.get('items', []):
                # Включаем только календари с правом чтения и редактирования
                access_role = cal.get('accessRole', '')
                if access_role in ['owner', 'writer', 'reader']:
                    calendars.append({
                        'id': cal.get('id'),
                        'summary': cal.get('summary', 'Без названия'),
                        'primary': cal.get('primary', False),
                        'access_role': access_role,
                    })
            self._calendars_cache = calendars
            return calendars
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Ошибка получения списка календарей: {e}")
            return [{'id': 'primary', 'summary': 'Primary', 'primary': True, 'access_role': 'owner'}]

    def get_emoji_for_title(self, title: str) -> str:
        """Подобрать эмодзи по названию события"""
        title_lower = title.lower()

        # Ищем совпадения с ключевыми словами
        for keyword, emoji in self.EMOJI_KEYWORDS.items():
            if keyword in title_lower:
                return emoji

        # По умолчанию — календарь
        return "🗓"

    def create_event(
        self,
        title: str,
        start_datetime: datetime,
        duration_minutes: int = 60,
        description: str = "",
        calendar_id: str = "primary",
        reminder_minutes: list[int] = None,
        location: str = None,
    ) -> dict:
        """Создать событие в календаре

        Args:
            reminder_minutes: Список минут до события для напоминаний.
                              Например: [1440, 60] = за день и за час
                              По умолчанию: [60, 15] (за час и за 15 минут)
            location: Место проведения события (адрес, офис и т.д.)
        """

        # Убеждаемся что datetime имеет timezone
        if start_datetime.tzinfo is None:
            start_datetime = self.timezone.localize(start_datetime)

        end_datetime = start_datetime + timedelta(minutes=duration_minutes)

        # Формируем напоминания
        if reminder_minutes is None:
            reminder_minutes = [60, 15]  # Дефолт: за час и за 15 минут

        reminders_overrides = [
            {"method": "popup", "minutes": mins} for mins in reminder_minutes
        ]

        event = {
            "summary": title,
            "description": description,
            "start": {
                "dateTime": start_datetime.isoformat(),
                "timeZone": config.TIMEZONE,
            },
            "end": {
                "dateTime": end_datetime.isoformat(),
                "timeZone": config.TIMEZONE,
            },
            "reminders": {
                "useDefault": False,
                "overrides": reminders_overrides,
            },
        }

        if location:
            event["location"] = location

        created_event = self.service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()

        return created_event

    def check_conflicts(
        self,
        start_datetime: datetime,
        end_datetime: datetime,
        calendar_id: str = "primary",
        exclude_event_id: str = None,
    ) -> list[dict]:
        """Проверить конфликты с существующими событиями"""

        if start_datetime.tzinfo is None:
            start_datetime = self.timezone.localize(start_datetime)
        if end_datetime.tzinfo is None:
            end_datetime = self.timezone.localize(end_datetime)

        # Получаем события в этом временном диапазоне
        events_result = self.service.events().list(
            calendarId=calendar_id,
            timeMin=start_datetime.isoformat(),
            timeMax=end_datetime.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        conflicts = []
        for event in events_result.get("items", []):
            # Пропускаем само событие (если обновляем)
            if exclude_event_id and event.get("id") == exclude_event_id:
                continue

            # Пропускаем события на весь день
            if "dateTime" not in event.get("start", {}):
                continue

            conflicts.append(event)

        return conflicts

    def format_conflict_warning(self, conflicts: list[dict]) -> str:
        """Форматировать предупреждение о конфликтах"""
        if not conflicts:
            return ""

        lines = ["⚠️ **Пересечение с:**"]
        for event in conflicts:
            start = event.get("start", {}).get("dateTime", "")
            if start:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                dt_local = dt.astimezone(self.timezone)
                time_str = dt_local.strftime("%H:%M")
                title = event.get("summary", "Без названия")
                lines.append(f"• {time_str} — {title}")

        return "\n".join(lines)

    def get_events(
        self,
        period: str = "today",
        calendar_id: str = "all",  # "all" = все календари, или конкретный ID
        max_results: int = 50,
        only_future: bool = True,
    ) -> list[dict]:
        """Получить события за период. only_future=True — только будущие события."""

        now = datetime.now(self.timezone)

        # Маппинг дней недели на номера (0 = понедельник)
        weekday_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }

        if period == "today":
            # Если only_future — берём от текущего времени, иначе от начала дня
            time_min = now if only_future else now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = now.replace(hour=23, minute=59, second=59, microsecond=0)
        elif period == "tomorrow":
            tomorrow = now + timedelta(days=1)
            time_min = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = tomorrow.replace(hour=23, minute=59, second=59, microsecond=0)
        elif period == "week":
            time_min = now if only_future else now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=7)
        elif period.lower() in weekday_map:
            # Конкретный день недели
            target_weekday = weekday_map[period.lower()]
            current_weekday = now.weekday()
            days_ahead = target_weekday - current_weekday
            if days_ahead <= 0:  # Если день уже прошёл на этой неделе — берём следующую
                days_ahead += 7
            target_date = now + timedelta(days=days_ahead)
            time_min = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = target_date.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            time_min = now if only_future else now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = now.replace(hour=23, minute=59, second=59, microsecond=0)

        # Определяем из каких календарей читать
        if calendar_id == "all":
            calendars = self.get_all_calendars()
            calendar_ids = [c['id'] for c in calendars]
        else:
            calendar_ids = [calendar_id]

        # Собираем события из всех календарей
        all_events = []
        for cal_id in calendar_ids:
            try:
                events_result = self.service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
                events = events_result.get("items", [])
                # Добавляем ID календаря к каждому событию (для удаления/редактирования)
                for event in events:
                    event['_calendar_id'] = cal_id
                all_events.extend(events)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Не удалось прочитать календарь {cal_id}: {e}")

        # Сортируем все события по времени начала
        def get_start_time(event):
            start = event.get("start", {})
            if "dateTime" in start:
                return start["dateTime"]
            return start.get("date", "9999")

        all_events.sort(key=get_start_time)

        return all_events

    def format_events_list(self, events: list[dict], period: str = "today") -> str:
        """Форматировать список событий для отображения"""
        import random

        now = datetime.now(self.timezone)

        # Маппинг английских дней на русские названия
        weekday_names = {
            "monday": "понедельник", "tuesday": "вторник", "wednesday": "среду",
            "thursday": "четверг", "friday": "пятницу", "saturday": "субботу", "sunday": "воскресенье"
        }
        is_weekday = period.lower() in weekday_names

        # Пустой список — живые комментарии
        if not events:
            if period == "today":
                empty_messages = [
                    "Пусто. Свободный вечер — можно расслабиться.",
                    "Ничего нет. Хочешь что-то добавить?",
                    "Чисто. Отдыхай или займись важным.",
                    "Задач нет. Редкий момент — цени его.",
                ]
            elif period == "tomorrow":
                empty_messages = [
                    "Завтра пусто. Планируем?",
                    "На завтра ничего. Можем что-то поставить.",
                    "Завтра свободен. Пока.",
                ]
            elif is_weekday:
                day_name = weekday_names[period.lower()]
                empty_messages = [
                    f"На {day_name} ничего нет.",
                    f"В {day_name} свободен.",
                ]
            else:
                empty_messages = [
                    "На неделю пусто. Затишье перед бурей?",
                    "Неделя свободна. Давай заполним.",
                ]
            return random.choice(empty_messages)

        # Есть события — формируем список
        lines = []

        # Краткая сводка в начале
        total = len(events)
        word = "дело" if total == 1 else "дела" if 2 <= total <= 4 else "событий"

        if period == "today":
            if total == 1:
                comment = "Одно дело — справишься."
            elif total <= 3:
                comment = f"{total} события. Норм день."
            elif total <= 5:
                comment = f"{total} событий. Плотненько."
            else:
                comment = f"{total} событий. Держись."
        elif period == "tomorrow":
            if total <= 2:
                comment = f"Завтра {total} {word}. Спокойный день."
            else:
                comment = f"Завтра {total} событий."
        elif is_weekday:
            day_name = weekday_names[period.lower()]
            # Для винительного падежа нужна корректная форма
            day_name_cap = day_name[0].upper() + day_name[1:]
            if total <= 2:
                comment = f"{day_name_cap} — {total} {word}. Спокойный день."
            else:
                comment = f"{day_name_cap} — {total} событий."
        else:
            comment = f"На неделе {total} событий."

        lines.append(comment)
        lines.append("")

        # Сортировка событий по времени начала
        def get_sort_key(event):
            start = event.get("start", {})
            if "dateTime" in start:
                return datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            elif "date" in start:
                # События на весь день — ставим в начало дня
                return datetime.fromisoformat(start["date"] + "T00:00:00+00:00")
            return datetime.min.replace(tzinfo=self.timezone)

        sorted_events = sorted(events, key=get_sort_key)

        # События
        for event in sorted_events:
            start = event.get("start", {})
            end = event.get("end", {})
            title = event.get("summary", "Без названия")
            emoji = self.get_emoji_for_title(title)

            if "dateTime" in start:
                start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                start_local = start_dt.astimezone(self.timezone)
                time_str = start_local.strftime("%H:%M")

                # Вычисляем время окончания
                end_str = ""
                if "dateTime" in end:
                    end_dt = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
                    end_local = end_dt.astimezone(self.timezone)
                    end_str = end_local.strftime("%H:%M")

                # Для недели добавляем день
                if period == "week":
                    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                    day = weekdays[start_local.weekday()]
                    lines.append(f"• {day} {time_str}–{end_str} — {emoji} {title}")
                else:
                    lines.append(f"• {time_str}–{end_str} — {emoji} {title}")
            else:
                # Весь день
                lines.append(f"• {emoji} {title} (весь день)")

        return "\n".join(lines)

    def parse_datetime_from_text(self, date_str: str, time_str: str) -> Optional[datetime]:
        """Парсинг даты и времени из текста"""

        now = datetime.now(self.timezone)

        # Словарь месяцев
        months_map = {
            "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
            "мая": 5, "июня": 6, "июля": 7, "августа": 8,
            "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
            "янв": 1, "фев": 2, "мар": 3, "апр": 4,
            "май": 5, "июн": 6, "июл": 7, "авг": 8,
            "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
        }

        # Словарь дней недели
        weekdays_map = {
            "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2,
            "четверг": 3, "пятница": 4, "пятницу": 4,
            "суббота": 5, "субботу": 5, "воскресенье": 6,
        }

        # Определяем дату
        if date_str is None or date_str.lower() in ["сегодня", "today", ""]:
            target_date = now.date()
        elif date_str.lower() in ["завтра", "tomorrow"]:
            target_date = (now + timedelta(days=1)).date()
        elif date_str.lower() in ["послезавтра"]:
            target_date = (now + timedelta(days=2)).date()
        elif date_str.lower() in weekdays_map:
            # День недели — находим ближайший
            target_weekday = weekdays_map[date_str.lower()]
            days_ahead = target_weekday - now.weekday()
            if days_ahead <= 0:  # Если уже прошёл — следующая неделя
                days_ahead += 7
            target_date = (now + timedelta(days=days_ahead)).date()
        elif any(date_str.lower().startswith(prefix) for prefix in ["следующий ", "следующая ", "следующую ", "следующее "]):
            # "следующий понедельник", "следующая среда" — день недели СЛЕДУЮЩЕЙ недели
            date_lower = date_str.lower()
            for prefix in ["следующий ", "следующая ", "следующую ", "следующее "]:
                if date_lower.startswith(prefix):
                    weekday_part = date_lower[len(prefix):]
                    break
            if weekday_part in weekdays_map:
                target_weekday = weekdays_map[weekday_part]
                days_ahead = target_weekday - now.weekday()
                # "следующая среда" = среда следующей недели (всегда +7 от текущего дня недели)
                if days_ahead < 0:
                    # День уже прошёл на этой неделе — +7 даёт следующую неделю
                    days_ahead += 7
                elif days_ahead == 0:
                    # Сегодня этот день — "следующий" значит через неделю
                    days_ahead = 7
                else:
                    # День ещё впереди на этой неделе — но "следующий" значит на следующей неделе
                    days_ahead += 7
                target_date = (now + timedelta(days=days_ahead)).date()
            else:
                target_date = now.date()
        else:
            # Пробуем парсить "26 декабря" или "26.12"
            target_date = None
            date_lower = date_str.lower()

            # Парсим "26 декабря" или "26декабря"
            for month_name, month_num in months_map.items():
                if month_name in date_lower:
                    # Извлекаем день
                    day_part = date_lower.replace(month_name, "").strip()
                    try:
                        day = int(day_part)
                        year = now.year
                        # Если дата уже прошла в этом году — берём следующий
                        candidate = now.replace(month=month_num, day=day).date()
                        if candidate < now.date():
                            year += 1
                        target_date = datetime(year, month_num, day).date()
                        break
                    except (ValueError, TypeError):
                        continue

            # Если не нашли — пробуем числовые форматы
            if target_date is None:
                for fmt in ["%d.%m", "%d.%m.%Y", "%d/%m", "%d/%m/%Y"]:
                    try:
                        parsed = datetime.strptime(date_str, fmt)
                        if fmt in ["%d.%m", "%d/%m"]:
                            parsed = parsed.replace(year=now.year)
                        target_date = parsed.date()
                        break
                    except ValueError:
                        continue

            # Фоллбэк — сегодня
            if target_date is None:
                target_date = now.date()

        # Определяем время
        if time_str is None or time_str == "":
            # Если время не указано, ставим через час
            target_time = (now + timedelta(hours=1)).time()
        else:
            try:
                # Форматы: "15:00", "15.00", "15 00", "15"
                time_str = time_str.replace(".", ":").replace(" ", ":")
                if ":" in time_str:
                    parts = time_str.split(":")
                    hour = int(parts[0])
                    minute = int(parts[1]) if len(parts) > 1 else 0
                else:
                    hour = int(time_str)
                    minute = 0

                # Валидация времени
                if not (0 <= hour <= 23):
                    logger.warning(f"Невалидный час: {hour}, используем текущий + 1")
                    target_time = (now + timedelta(hours=1)).time()
                elif not (0 <= minute <= 59):
                    logger.warning(f"Невалидная минута: {minute}, используем 0")
                    target_time = datetime.now().replace(hour=hour, minute=0, second=0).time()
                else:
                    target_time = datetime.now().replace(hour=hour, minute=minute, second=0).time()
            except Exception as e:
                logger.warning(f"Ошибка парсинга времени '{time_str}': {e}")
                target_time = (now + timedelta(hours=1)).time()

        result = datetime.combine(target_date, target_time)
        return self.timezone.localize(result)

    def find_event_by_title(self, title_part: str, calendar_id: str = "all", search_days: int = 30) -> Optional[dict]:
        """Найти событие по части названия (ищем на 30 дней вперёд во всех календарях)"""
        now = datetime.now(self.timezone)
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min + timedelta(days=search_days)

        # Определяем календари для поиска
        if calendar_id == "all":
            calendars = self.get_all_calendars()
            calendar_ids = [c['id'] for c in calendars]
        else:
            calendar_ids = [calendar_id]

        title_lower = title_part.lower()

        for cal_id in calendar_ids:
            try:
                events_result = self.service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    maxResults=50,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()

                events = events_result.get("items", [])

                for event in events:
                    summary = event.get("summary", "").lower()
                    if title_lower in summary:
                        event['_calendar_id'] = cal_id  # Сохраняем ID календаря
                        return event
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Не удалось прочитать календарь {cal_id}: {e}")

        return None

    def find_all_events_by_title(self, title_part: str, calendar_id: str = "all", search_days: int = 365) -> list[dict]:
        """Найти ВСЕ события по части названия (для удаления со всех дат, во всех календарях)"""
        now = datetime.now(self.timezone)
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min + timedelta(days=search_days)

        # Определяем календари для поиска
        if calendar_id == "all":
            calendars = self.get_all_calendars()
            calendar_ids = [c['id'] for c in calendars]
        else:
            calendar_ids = [calendar_id]

        title_lower = title_part.lower()
        matched = []

        for cal_id in calendar_ids:
            try:
                events_result = self.service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    maxResults=200,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()

                events = events_result.get("items", [])

                for event in events:
                    summary = event.get("summary", "").lower()
                    if title_lower in summary:
                        event['_calendar_id'] = cal_id  # Сохраняем ID календаря
                        matched.append(event)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Не удалось прочитать календарь {cal_id}: {e}")

        return matched

    def search_events(
        self,
        query: str,
        period: str = "month",
        calendar_id: str = "primary",
        max_results: int = 20,
    ) -> list[dict]:
        """
        Умный поиск событий по запросу.
        Ищет по названию, описанию и ключевым словам.
        """
        now = datetime.now(self.timezone)

        # Определяем период поиска
        if period == "today":
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = now.replace(hour=23, minute=59, second=59, microsecond=0)
        elif period == "week":
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=7)
        elif period == "month":
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=30)
        elif period == "past_week":
            time_max = now
            time_min = now - timedelta(days=7)
        elif period == "past_month":
            time_max = now
            time_min = now - timedelta(days=30)
        elif period == "all":
            time_min = now - timedelta(days=365)
            time_max = now + timedelta(days=365)
        else:
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=30)

        # Получаем все события за период
        events_result = self.service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=100,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        all_events = events_result.get("items", [])
        query_lower = query.lower()

        # Разбиваем запрос на слова для более гибкого поиска
        query_words = query_lower.split()

        # Фильтруем и сортируем по релевантности
        scored_events = []
        for event in all_events:
            summary = event.get("summary", "").lower()
            description = event.get("description", "").lower()
            location = event.get("location", "").lower()

            score = 0

            # Точное совпадение в названии — высший приоритет
            if query_lower in summary:
                score += 100

            # Все слова запроса есть в названии
            if all(word in summary for word in query_words):
                score += 50

            # Частичные совпадения слов
            for word in query_words:
                if word in summary:
                    score += 20
                if word in description:
                    score += 5
                if word in location:
                    score += 3

            if score > 0:
                scored_events.append((score, event))

        # Сортируем по релевантности
        scored_events.sort(key=lambda x: x[0], reverse=True)

        return [event for score, event in scored_events[:max_results]]

    def format_search_results(self, events: list[dict], query: str) -> str:
        """Форматировать результаты поиска"""
        if not events:
            return f"🔍 По запросу «{query}» ничего не найдено."

        weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]

        total = len(events)
        word = "событие" if total == 1 else "события" if 2 <= total <= 4 else "событий"
        lines = [f"🔍 Найдено {total} {word} по запросу «{query}»:\n"]

        now = datetime.now(self.timezone)

        for i, event in enumerate(events[:10], 1):  # Максимум 10 результатов
            start = event.get("start", {})
            title = event.get("summary", "Без названия")
            emoji = self.get_emoji_for_title(title)

            if "dateTime" in start:
                start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                start_local = start_dt.astimezone(self.timezone)
                weekday = weekdays[start_local.weekday()]
                time_str = start_local.strftime("%H:%M")

                # Определяем относительную дату
                days_diff = (start_local.date() - now.date()).days
                if days_diff == 0:
                    date_label = "сегодня"
                elif days_diff == 1:
                    date_label = "завтра"
                elif days_diff == -1:
                    date_label = "вчера"
                elif days_diff < 0:
                    date_label = f"{start_local.day} {months[start_local.month-1]}"
                else:
                    date_label = f"{start_local.day} {months[start_local.month-1]}"

                lines.append(f"{i}. {emoji} {title}")
                lines.append(f"   📅 {date_label} ({weekday}) в {time_str}")
            else:
                # Событие на весь день
                if "date" in start:
                    date_str = start["date"]
                    try:
                        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                        weekday = weekdays[date_obj.weekday()]
                        date_label = f"{date_obj.day} {months[date_obj.month-1]}"
                        lines.append(f"{i}. {emoji} {title}")
                        lines.append(f"   📅 {date_label} ({weekday}), весь день")
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Ошибка парсинга даты события '{date_str}': {e}")
                        lines.append(f"{i}. {emoji} {title}")
                else:
                    lines.append(f"{i}. {emoji} {title}")

            lines.append("")

        if total > 10:
            lines.append(f"_...и ещё {total - 10} событий_")

        return "\n".join(lines).strip()

    def update_event_time(
        self,
        event_id: str,
        new_datetime: datetime,
        duration_minutes: int = None,
        calendar_id: str = "primary",
    ) -> dict:
        """Обновить время события"""

        # Получаем текущее событие
        event = self.service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        # Вычисляем длительность если не указана
        if duration_minutes is None:
            start = event.get("start", {})
            end = event.get("end", {})
            if "dateTime" in start and "dateTime" in end:
                start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
                duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
            else:
                duration_minutes = 60

        if new_datetime.tzinfo is None:
            new_datetime = self.timezone.localize(new_datetime)

        end_datetime = new_datetime + timedelta(minutes=duration_minutes)

        event["start"] = {
            "dateTime": new_datetime.isoformat(),
            "timeZone": config.TIMEZONE,
        }
        event["end"] = {
            "dateTime": end_datetime.isoformat(),
            "timeZone": config.TIMEZONE,
        }

        updated_event = self.service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()

        return updated_event

    def update_event_reminders(
        self,
        event_id: str,
        reminder_minutes: list[int],
        calendar_id: str = "primary",
    ) -> dict:
        """Обновить напоминания события"""

        event = self.service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        reminders_overrides = [
            {"method": "popup", "minutes": mins} for mins in reminder_minutes
        ]

        event["reminders"] = {
            "useDefault": False,
            "overrides": reminders_overrides,
        }

        updated_event = self.service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()

        return updated_event

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> bool:
        """Удалить событие из календаря"""
        import logging
        logger = logging.getLogger(__name__)
        try:
            logger.info(f"🗑️ Удаляю событие: event_id={event_id}, calendar_id={calendar_id}")
            self.service.events().delete(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            logger.info(f"🗑️ Событие {event_id} успешно удалено из Google Calendar")
            return True
        except Exception as e:
            logger.error(f"🗑️ Ошибка удаления события {event_id}: {e}")
            return False

    def rename_event(self, event_id: str, new_title: str, calendar_id: str = "primary") -> dict:
        """Переименовать событие"""
        event = self.service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        event["summary"] = new_title

        updated_event = self.service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()

        return updated_event

    def create_recurring_event(
        self,
        title: str,
        start_datetime: datetime,
        duration_minutes: int = 60,
        recurrence: str = "weekly",
        calendar_id: str = "primary",
        reminder_minutes: list[int] = None,
        location: str = None,
    ) -> dict:
        """Создать повторяющееся событие"""

        if start_datetime.tzinfo is None:
            start_datetime = self.timezone.localize(start_datetime)

        end_datetime = start_datetime + timedelta(minutes=duration_minutes)

        # Формируем правило повторения (RRULE)
        recurrence_rules = {
            "daily": "RRULE:FREQ=DAILY",
            "weekly": f"RRULE:FREQ=WEEKLY;BYDAY={self._get_weekday_code(start_datetime)}",
            "monthly": f"RRULE:FREQ=MONTHLY;BYMONTHDAY={start_datetime.day}",
        }

        rrule = recurrence_rules.get(recurrence, recurrence_rules["weekly"])

        # Формируем напоминания
        if reminder_minutes is None:
            reminder_minutes = [60, 15]

        reminders_overrides = [
            {"method": "popup", "minutes": mins} for mins in reminder_minutes
        ]

        event = {
            "summary": title,
            "start": {
                "dateTime": start_datetime.isoformat(),
                "timeZone": config.TIMEZONE,
            },
            "end": {
                "dateTime": end_datetime.isoformat(),
                "timeZone": config.TIMEZONE,
            },
            "recurrence": [rrule],
            "reminders": {
                "useDefault": False,
                "overrides": reminders_overrides,
            },
        }

        if location:
            event["location"] = location

        created_event = self.service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()

        return created_event

    def _get_weekday_code(self, dt: datetime) -> str:
        """Получить код дня недели для RRULE"""
        codes = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
        return codes[dt.weekday()]

    def find_free_slots(
        self,
        date_str: str = "сегодня",
        min_duration_minutes: int = 60,
        work_start: int = 9,
        work_end: int = 21,
    ) -> list[dict]:
        """Найти свободные слоты в расписании"""

        now = datetime.now(self.timezone)

        # Определяем дату
        if date_str in ["сегодня", "today", None]:
            target_date = now.date()
        elif date_str in ["завтра", "tomorrow"]:
            target_date = (now + timedelta(days=1)).date()
        else:
            # Пробуем распарсить дату в формате YYYY-MM-DD
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = now.date()

        # Границы рабочего дня
        day_start = self.timezone.localize(
            datetime.combine(target_date, datetime.min.time().replace(hour=work_start))
        )
        day_end = self.timezone.localize(
            datetime.combine(target_date, datetime.min.time().replace(hour=work_end))
        )

        # Если сегодня и уже позже начала — начинаем с текущего времени
        if target_date == now.date() and now > day_start:
            # Округляем до следующего получаса
            minutes = now.minute
            if minutes < 30:
                day_start = now.replace(minute=30, second=0, microsecond=0)
            else:
                day_start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        # Получаем события на этот день
        events_result = self.service.events().list(
            calendarId="primary",
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])

        # Собираем занятые интервалы
        busy_intervals = []
        for event in events:
            start = event.get("start", {})
            end = event.get("end", {})

            if "dateTime" in start and "dateTime" in end:
                start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
                busy_intervals.append((start_dt, end_dt))

        # Сортируем по времени начала
        busy_intervals.sort(key=lambda x: x[0])

        # Ищем свободные слоты
        free_slots = []
        current_time = day_start

        for busy_start, busy_end in busy_intervals:
            # Если есть свободное время до следующего события
            if current_time < busy_start:
                gap_minutes = (busy_start - current_time).total_seconds() / 60
                if gap_minutes >= min_duration_minutes:
                    free_slots.append({
                        "start": current_time,
                        "end": busy_start,
                        "duration_minutes": int(gap_minutes),
                    })
            current_time = max(current_time, busy_end)

        # Проверяем время после последнего события
        if current_time < day_end:
            gap_minutes = (day_end - current_time).total_seconds() / 60
            if gap_minutes >= min_duration_minutes:
                free_slots.append({
                    "start": current_time,
                    "end": day_end,
                    "duration_minutes": int(gap_minutes),
                })

        return free_slots

    def format_free_slots(self, slots: list[dict], date_str: str = "сегодня") -> str:
        """Форматировать список свободных слотов"""
        if not slots:
            return f"😕 На {date_str} нет свободных окон нужной длительности."

        weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

        lines = [f"🕐 Свободное время ({date_str}):\n"]

        for slot in slots[:5]:  # Максимум 5 слотов
            start = slot["start"]
            end = slot["end"]
            duration = slot["duration_minutes"]

            start_str = start.strftime("%H:%M")
            end_str = end.strftime("%H:%M")

            # Форматируем длительность
            if duration >= 60:
                hours = duration // 60
                mins = duration % 60
                if mins > 0:
                    dur_str = f"{hours}ч {mins}мин"
                else:
                    dur_str = f"{hours}ч"
            else:
                dur_str = f"{duration}мин"

            lines.append(f"• {start_str} – {end_str} ({dur_str})")

        return "\n".join(lines)
