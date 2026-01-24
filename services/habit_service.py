"""
Сервис для работы с привычками и геймификацией.
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Habit, HabitLog, UserStats, User


# Константы геймификации
XP_PER_HABIT = 10  # XP за выполнение привычки
XP_PER_STREAK_DAY = 5  # Бонус за каждый день стрика
XP_PER_LEVEL = 100  # XP для следующего уровня

# Ачивки
ACHIEVEMENTS = {
    "first_habit": {"name": "Первый шаг", "emoji": "🎯", "description": "Создал первую привычку"},
    "first_checkin": {"name": "Начало пути", "emoji": "✨", "description": "Первый чек-ин"},
    "streak_3": {"name": "3 дня подряд", "emoji": "🔥", "description": "Стрик 3 дня"},
    "streak_7": {"name": "Неделя силы", "emoji": "💪", "description": "Стрик 7 дней"},
    "streak_30": {"name": "Месяц дисциплины", "emoji": "🏆", "description": "Стрик 30 дней"},
    "level_5": {"name": "Продвинутый", "emoji": "⭐", "description": "Достигнут 5 уровень"},
    "level_10": {"name": "Мастер привычек", "emoji": "🌟", "description": "Достигнут 10 уровень"},
    "all_habits_day": {"name": "Идеальный день", "emoji": "💯", "description": "Все привычки за день"},
}


class HabitService:
    """Сервис для управления привычками"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_stats(self, user_id: int) -> UserStats:
        """Получить или создать статистику пользователя"""
        result = await self.session.execute(
            select(UserStats).where(UserStats.user_id == user_id)
        )
        stats = result.scalar_one_or_none()

        if not stats:
            stats = UserStats(user_id=user_id, achievements={})
            self.session.add(stats)
            await self.session.commit()
            await self.session.refresh(stats)

        return stats

    async def create_habit(
        self,
        user_id: int,
        name: str,
        emoji: str = "✅",
        target_value: Optional[int] = None,
        unit: Optional[str] = None,
        frequency: str = "daily"
    ) -> Habit | None:
        """Создать новую привычку. Возвращает None если такая уже есть."""
        # Проверяем, нет ли уже такой привычки
        existing = await self.session.execute(
            select(Habit).where(
                Habit.user_id == user_id,
                Habit.name == name,
                Habit.is_active == True
            )
        )
        if existing.scalar_one_or_none():
            return None  # Уже есть такая привычка

        habit = Habit(
            user_id=user_id,
            name=name,
            emoji=emoji,
            target_value=target_value,
            unit=unit,
            frequency=frequency
        )
        self.session.add(habit)
        await self.session.commit()
        await self.session.refresh(habit)

        # Проверяем ачивку "Первая привычка"
        await self._check_achievement(user_id, "first_habit")

        return habit

    async def get_user_habits(self, user_id: int, active_only: bool = True) -> list[Habit]:
        """Получить привычки пользователя"""
        query = select(Habit).where(Habit.user_id == user_id)
        if active_only:
            query = query.where(Habit.is_active == True)
        query = query.order_by(Habit.created_at)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def log_habit(
        self,
        habit_id: int,
        user_id: int,
        value: int = 1,
        note: Optional[str] = None
    ) -> tuple[HabitLog, int, list[str]]:
        """
        Залогировать выполнение привычки.
        Возвращает: (лог, полученный XP, список новых ачивок)
        """
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Проверяем, не залогировано ли уже сегодня
        existing = await self.session.execute(
            select(HabitLog).where(
                HabitLog.habit_id == habit_id,
                HabitLog.user_id == user_id,
                HabitLog.date >= today
            )
        )
        existing_log = existing.scalar_one_or_none()
        if existing_log:
            # Обновляем существующий лог
            existing_log.value = value
            existing_log.note = note
            await self.session.commit()
            return existing_log, 0, []

        # Создаём новый лог
        log = HabitLog(
            habit_id=habit_id,
            user_id=user_id,
            value=value,
            note=note,
            date=datetime.now()
        )
        self.session.add(log)

        # Обновляем статистику
        stats = await self.get_or_create_stats(user_id)
        xp_earned = XP_PER_HABIT
        new_achievements = []

        # Проверяем стрик
        if stats.last_activity_date:
            yesterday = today - timedelta(days=1)
            if stats.last_activity_date.date() == yesterday.date():
                # Продолжаем стрик
                stats.current_streak += 1
                xp_earned += XP_PER_STREAK_DAY * stats.current_streak
            elif stats.last_activity_date.date() < yesterday.date():
                # Стрик сбросился
                stats.current_streak = 1
        else:
            stats.current_streak = 1

        # Обновляем рекорд стрика
        if stats.current_streak > stats.longest_streak:
            stats.longest_streak = stats.current_streak

        stats.last_activity_date = datetime.now()
        stats.xp += xp_earned

        # Проверяем уровень
        new_level = (stats.xp // XP_PER_LEVEL) + 1
        if new_level > stats.level:
            stats.level = new_level

        await self.session.commit()

        # Проверяем ачивки
        new_achievements.extend(await self._check_achievement(user_id, "first_checkin"))

        if stats.current_streak >= 3:
            new_achievements.extend(await self._check_achievement(user_id, "streak_3"))
        if stats.current_streak >= 7:
            new_achievements.extend(await self._check_achievement(user_id, "streak_7"))
        if stats.current_streak >= 30:
            new_achievements.extend(await self._check_achievement(user_id, "streak_30"))
        if stats.level >= 5:
            new_achievements.extend(await self._check_achievement(user_id, "level_5"))
        if stats.level >= 10:
            new_achievements.extend(await self._check_achievement(user_id, "level_10"))

        # Проверяем "идеальный день"
        if await self._check_all_habits_done(user_id):
            new_achievements.extend(await self._check_achievement(user_id, "all_habits_day"))

        return log, xp_earned, new_achievements

    async def _check_achievement(self, user_id: int, achievement_key: str) -> list[str]:
        """Проверить и выдать ачивку, если ещё не получена"""
        stats = await self.get_or_create_stats(user_id)
        achievements = stats.achievements or {}

        if achievement_key not in achievements:
            achievements[achievement_key] = True
            stats.achievements = achievements
            await self.session.commit()
            return [achievement_key]

        return []

    async def _check_all_habits_done(self, user_id: int) -> bool:
        """Проверить, все ли привычки выполнены сегодня"""
        habits = await self.get_user_habits(user_id)
        if not habits:
            return False

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        for habit in habits:
            result = await self.session.execute(
                select(HabitLog).where(
                    HabitLog.habit_id == habit.id,
                    HabitLog.date >= today
                )
            )
            if not result.scalar_one_or_none():
                return False

        return True

    async def get_today_status(self, user_id: int) -> dict:
        """Получить статус привычек на сегодня"""
        import json

        habits = await self.get_user_habits(user_id)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Получаем режим пользователя для расчёта интервальных привычек
        user_result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()
        morning_time = user.morning_time if user else "08:00"
        evening_time = user.evening_time if user else "22:00"

        status = []
        for habit in habits:
            result = await self.session.execute(
                select(HabitLog).where(
                    HabitLog.habit_id == habit.id,
                    HabitLog.date >= today
                )
            )
            log = result.scalar_one_or_none()

            # Определяем эффективный target:
            # 1. Явный target_value
            # 2. Количество интервалов (если reminder_interval_minutes)
            # 3. Количество напоминаний (если reminder_times > 1)
            effective_target = habit.target_value

            # Для интервальных привычек (вода каждый час)
            if effective_target is None and habit.reminder_interval_minutes:
                try:
                    start_h = int(morning_time.split(":")[0])
                    end_h = int(evening_time.split(":")[0])
                    total_minutes = (end_h - start_h) * 60
                    effective_target = total_minutes // habit.reminder_interval_minutes
                except (ValueError, AttributeError):
                    effective_target = None

            # Для привычек с несколькими фиксированными напоминаниями
            if effective_target is None and habit.reminder_times:
                try:
                    times = json.loads(habit.reminder_times)
                    if isinstance(times, list) and len(times) > 1:
                        effective_target = len(times)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Для привычек с target — done только когда достигнута цель
            if effective_target:
                is_done = log is not None and log.value >= effective_target
            else:
                is_done = log is not None

            status.append({
                "habit": habit,
                "done": is_done,
                "value": log.value if log else 0,
                "target": effective_target,  # Добавляем target для отображения
            })

        stats = await self.get_or_create_stats(user_id)

        return {
            "habits": status,
            "stats": stats,
            "total": len(habits),
            "completed": sum(1 for s in status if s["done"]),
        }

    async def get_streak_info(self, user_id: int) -> dict:
        """Получить информацию о стриках"""
        stats = await self.get_or_create_stats(user_id)

        return {
            "current": stats.current_streak,
            "longest": stats.longest_streak,
            "xp": stats.xp,
            "level": stats.level,
            "xp_to_next": XP_PER_LEVEL - (stats.xp % XP_PER_LEVEL),
        }

    async def delete_habit(self, habit_id: int, user_id: int) -> bool:
        """Удалить привычку (деактивировать)"""
        result = await self.session.execute(
            select(Habit).where(
                Habit.id == habit_id,
                Habit.user_id == user_id
            )
        )
        habit = result.scalar_one_or_none()

        if habit:
            habit.is_active = False
            await self.session.commit()
            return True

        return False

    def format_habits_message(self, status: dict) -> str:
        """Форматировать сообщение со статусом привычек — минималистичный дизайн"""

        if not status["habits"]:
            return (
                "У тебя пока нет привычек.\n"
                "Добавь: `/habit_add Спорт`"
            )

        lines = []

        # Считаем реальный прогресс (с учётом частичного выполнения)
        total_progress = 0
        total_max = 0

        # Список привычек — чисто и минималистично
        for item in status["habits"]:
            habit = item["habit"]
            done = item["done"]
            value = item["value"]
            effective_target = item.get("target")  # Уже рассчитано в get_today_status

            # Галочка только когда выполнено
            check = "✅ " if done else ""

            # Прогресс для привычек с целью (кроме сна)
            habit_name_lower = habit.name.lower()
            if effective_target and "сон" not in habit_name_lower:
                progress_text = f" ({value}/{effective_target})"
                # Ограничиваем прогресс целью (максимум 100%)
                total_progress += min(value, effective_target)
                total_max += effective_target
            else:
                # Простая привычка без цели — считаем как 0 или 1
                progress_text = ""
                total_progress += 1 if done else 0
                total_max += 1

            lines.append(f"{check}{habit.emoji} {habit.name}{progress_text}")

        # Разделитель
        lines.append("")

        # Прогресс с учётом частичного выполнения
        pct = int((total_progress / total_max) * 100) if total_max > 0 else 0
        lines.append(f"Сегодня: {total_progress}/{total_max} ({pct}%)")

        # Стрик и уровень
        stats = status["stats"]
        if stats.current_streak > 0:
            days_word = "день" if stats.current_streak == 1 else "дня" if 2 <= stats.current_streak <= 4 else "дней"
            lines.append(f"🔥 {stats.current_streak} {days_word} подряд")

        return "\n".join(lines)

    def format_achievement_message(self, achievement_key: str) -> str:
        """Форматировать сообщение о новой ачивке"""
        ach = ACHIEVEMENTS.get(achievement_key, {})
        return (
            f"🎉 **Новая ачивка!**\n\n"
            f"{ach.get('emoji', '🏆')} **{ach.get('name', 'Ачивка')}**\n"
            f"_{ach.get('description', '')}_"
        )


# Стандартные привычки для быстрого добавления
DEFAULT_HABITS = [
    {"name": "Спорт", "emoji": "🏃", "target_value": None, "unit": None},
    {"name": "Вода", "emoji": "💧", "target_value": None, "unit": None},  # Теперь с интервалом напоминаний
    {"name": "Медитация", "emoji": "🧘", "target_value": None, "unit": None},
    {"name": "Чтение", "emoji": "📚", "target_value": 30, "unit": "минут"},
    {"name": "Сон", "emoji": "😴", "target_value": 8, "unit": "часов"},
    {"name": "Витамины", "emoji": "💊", "target_value": None, "unit": None},
    {"name": "Прогулка", "emoji": "🚶", "target_value": None, "unit": None},
    {"name": "Без соцсетей", "emoji": "📵", "target_value": None, "unit": None},
]
