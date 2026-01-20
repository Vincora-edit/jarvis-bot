"""
Сервис умных напоминаний.
Контекстные напоминания с адаптивным временем и подготовкой.
"""
import re
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Категории событий и их настройки
# Напоминания: только за 1 час и 15 минут (по запросу пользователя)
EVENT_CATEGORIES = {
    "meeting": {
        "keywords": ["созвон", "встреча", "митинг", "синк", "звонок", "колл", "call", "meeting", "sync"],
        "emoji": "📞",
        "remind_minutes": [60, 15],
        "prep_message": "Подготовься к звонку: проверь камеру и микрофон",
    },
    "work": {
        "keywords": ["работа", "задача", "дедлайн", "сдать", "отправить", "презентация"],
        "emoji": "💼",
        "remind_minutes": [60, 15],
        "prep_message": "Проверь, что всё готово к дедлайну",
    },
    "health": {
        "keywords": ["врач", "доктор", "клиника", "больница", "анализы", "прием", "терапия", "стоматолог", "массаж"],
        "emoji": "🏥",
        "remind_minutes": [60, 15],
        "prep_message": "Не забудь документы и полис",
    },
    "sport": {
        "keywords": ["тренировка", "спорт", "зал", "бег", "йога", "бассейн", "футбол", "теннис"],
        "emoji": "🏃",
        "remind_minutes": [60, 15],
        "prep_message": "Приготовь спортивную форму и воду",
    },
    "personal": {
        "keywords": ["день рождения", "праздник", "вечеринка", "ужин", "обед", "кино", "театр"],
        "emoji": "🎉",
        "remind_minutes": [60, 15],
        "prep_message": "Не забудь подарок!",
    },
    "travel": {
        "keywords": ["поезд", "самолет", "рейс", "вокзал", "аэропорт", "такси", "трансфер"],
        "emoji": "✈️",
        "remind_minutes": [60, 15],
        "prep_message": "Проверь билеты и документы",
    },
    "study": {
        "keywords": ["урок", "лекция", "курс", "учеба", "экзамен", "семинар", "вебинар"],
        "emoji": "📚",
        "remind_minutes": [60, 15],
        "prep_message": "Подготовь материалы к занятию",
    },
    "default": {
        "keywords": [],
        "emoji": "📅",
        "remind_minutes": [60, 15],
        "prep_message": None,
    }
}

# Контекстные сообщения для напоминаний
REMINDER_TEMPLATES = {
    "long": [  # Больше 60 минут
        "Через {time}: {event}",
        "{event} через {time}",
    ],
    "medium": [  # 15-60 минут
        "{time} до: {event}",
        "Через {time}: {event}",
    ],
    "short": [  # Меньше 15 минут
        "{time}: {event}",
        "Скоро: {event}",
    ],
}


class SmartReminderService:
    """Сервис умных контекстных напоминаний"""

    def detect_category(self, title: str) -> str:
        """Определить категорию события по названию"""
        title_lower = title.lower()

        for category, config in EVENT_CATEGORIES.items():
            if category == "default":
                continue
            for keyword in config["keywords"]:
                if keyword in title_lower:
                    return category

        return "default"

    def get_reminder_times(self, title: str) -> list[int]:
        """Получить список времён напоминаний для события (в минутах)"""
        category = self.detect_category(title)
        return EVENT_CATEGORIES[category]["remind_minutes"]

    def should_remind(self, title: str, minutes_until: int, tolerance: int = 2) -> bool:
        """Проверить, нужно ли напомнить сейчас"""
        reminder_times = self.get_reminder_times(title)

        for remind_at in reminder_times:
            if remind_at - tolerance <= minutes_until <= remind_at + tolerance:
                return True

        return False

    def format_time_until(self, minutes: int) -> str:
        """Форматировать время до события"""
        if minutes >= 120:
            hours = minutes // 60
            return f"{hours} ч"
        elif minutes >= 60:
            hours = minutes // 60
            mins = minutes % 60
            if mins > 0:
                return f"{hours} ч {mins} мин"
            return f"{hours} час"
        elif minutes >= 10:
            return f"{minutes} мин"
        else:
            return f"{minutes} минут"

    def generate_reminder(
        self,
        title: str,
        event_time: datetime,
        minutes_until: int,
        include_prep: bool = True
    ) -> str:
        """Сгенерировать сообщение напоминания"""
        import random

        category = self.detect_category(title)
        config = EVENT_CATEGORIES[category]
        emoji = config["emoji"]

        # Выбираем шаблон в зависимости от времени
        if minutes_until > 60:
            templates = REMINDER_TEMPLATES["long"]
        elif minutes_until > 15:
            templates = REMINDER_TEMPLATES["medium"]
        else:
            templates = REMINDER_TEMPLATES["short"]

        template = random.choice(templates)
        time_str = self.format_time_until(minutes_until)

        message = template.format(
            time=time_str,
            event=f"{emoji} {title}"
        )

        # Добавляем время события
        event_time_str = event_time.strftime("%H:%M")
        message += f"\n📍 Время: {event_time_str}"

        # Добавляем подсказку для подготовки (только для первого напоминания)
        if include_prep and config["prep_message"]:
            remind_times = config["remind_minutes"]
            # Если это первое (самое раннее) напоминание
            if minutes_until >= max(remind_times) - 5:
                message += f"\n\n💡 {config['prep_message']}"

        return message

    def get_next_reminder_time(self, title: str, event_time: datetime, now: datetime) -> Optional[datetime]:
        """Получить время следующего напоминания"""
        reminder_times = self.get_reminder_times(title)
        minutes_until = (event_time - now).total_seconds() / 60

        # Находим следующее время напоминания
        for remind_at in sorted(reminder_times, reverse=True):
            if minutes_until > remind_at:
                return event_time - timedelta(minutes=remind_at)

        return None

    def analyze_day_events(self, events: list) -> dict:
        """Анализ событий дня для умного планирования"""
        categories = {}
        busy_hours = []

        for event in events:
            title = event.get("summary", "")
            start = event.get("start", {})

            category = self.detect_category(title)
            categories[category] = categories.get(category, 0) + 1

            if "dateTime" in start:
                start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                busy_hours.append(start_dt.hour)

        return {
            "categories": categories,
            "busy_hours": busy_hours,
            "total_events": len(events),
            "busiest_category": max(categories, key=categories.get) if categories else None
        }

    def generate_day_summary(self, analysis: dict) -> str:
        """Сгенерировать краткую сводку дня"""
        total = analysis["total_events"]

        if total == 0:
            return "📭 Сегодня свободный день!"

        category_emoji = {
            "meeting": "📞",
            "work": "💼",
            "health": "🏥",
            "sport": "🏃",
            "personal": "🎉",
            "travel": "✈️",
            "study": "📚",
            "default": "📅"
        }

        lines = [f"📊 На сегодня {total} событий:"]
        for cat, count in analysis["categories"].items():
            emoji = category_emoji.get(cat, "📅")
            lines.append(f"  {emoji} {cat}: {count}")

        if analysis["busy_hours"]:
            peak_hour = max(set(analysis["busy_hours"]), key=analysis["busy_hours"].count)
            lines.append(f"\n⚡ Пиковое время: {peak_hour}:00")

        return "\n".join(lines)
