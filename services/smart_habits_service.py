"""
Сервис смарт-привычек (уровень 3).
Анализирует поведение пользователя и адаптирует время напоминаний.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

import pytz
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Habit, HabitLog, User
from config import config

logger = logging.getLogger(__name__)


class SmartHabitsService:
    """Сервис адаптивных напоминаний для привычек"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.timezone = pytz.timezone(config.TIMEZONE)

    async def analyze_habit_patterns(self, habit: Habit, days: int = 14) -> dict:
        """
        Анализирует когда пользователь обычно выполняет привычку.
        Возвращает паттерны по дням недели.
        """
        now = datetime.now(self.timezone)
        start_date = now - timedelta(days=days)

        # Получаем все логи за период
        result = await self.session.execute(
            select(HabitLog).where(
                and_(
                    HabitLog.habit_id == habit.id,
                    HabitLog.date >= start_date
                )
            ).order_by(HabitLog.date)
        )
        logs = result.scalars().all()

        if not logs:
            return {}

        # Группируем по дням недели и времени
        patterns = defaultdict(list)  # {weekday: [times]}

        for log in logs:
            log_dt = log.date
            if log_dt.tzinfo is None:
                log_dt = self.timezone.localize(log_dt)
            else:
                log_dt = log_dt.astimezone(self.timezone)

            weekday = log_dt.weekday()  # 0=Пн, 6=Вс
            time_str = log_dt.strftime("%H:%M")
            patterns[weekday].append(time_str)

        return dict(patterns)

    def calculate_optimal_time(self, times: list[str]) -> str:
        """
        Вычисляет оптимальное время напоминания на основе истории.
        Возвращает среднее время минус 15 минут (чтобы напомнить ДО).
        """
        if not times:
            return "09:00"

        # Конвертируем в минуты от полуночи
        minutes_list = []
        for t in times:
            try:
                h, m = map(int, t.split(":"))
                minutes_list.append(h * 60 + m)
            except ValueError:
                continue

        if not minutes_list:
            return "09:00"

        # Среднее время
        avg_minutes = sum(minutes_list) // len(minutes_list)

        # Напоминаем на 15 минут раньше
        reminder_minutes = max(0, avg_minutes - 15)

        # Округляем до 15 минут
        reminder_minutes = (reminder_minutes // 15) * 15

        hours = reminder_minutes // 60
        mins = reminder_minutes % 60

        return f"{hours:02d}:{mins:02d}"

    async def update_learned_times(self, habit: Habit) -> dict:
        """
        Обновляет выученное время для привычки на основе истории.
        Возвращает новые времена по дням недели.
        """
        patterns = await self.analyze_habit_patterns(habit)

        if not patterns:
            return {}

        learned = {}
        for weekday, times in patterns.items():
            if len(times) >= 2:  # Минимум 2 точки данных
                optimal = self.calculate_optimal_time(times)
                learned[str(weekday)] = [optimal]

        # Сохраняем в привычку
        habit.learned_times = json.dumps(learned) if learned else None
        habit.last_reminder_adjust = datetime.now(self.timezone)
        await self.session.commit()

        logger.info(f"Обновлены выученные времена для привычки {habit.id}: {learned}")
        return learned

    async def get_reminder_time(self, habit: Habit, weekday: int = None) -> Optional[str]:
        """
        Получает время напоминания для привычки.
        Приоритет: выученное > персональное > дефолтное.
        """
        if weekday is None:
            weekday = datetime.now(self.timezone).weekday()

        # 1. Пробуем выученное время
        if habit.learned_times:
            try:
                learned = json.loads(habit.learned_times)
                if str(weekday) in learned:
                    times = learned[str(weekday)]
                    if times:
                        return times[0]
            except (json.JSONDecodeError, KeyError):
                pass

        # 2. Пробуем персональное время
        if habit.reminder_times:
            try:
                times = json.loads(habit.reminder_times)
                if times:
                    return times[0]
            except (json.JSONDecodeError, IndexError):
                pass

        # 3. Дефолтное время на основе типа привычки
        return self._get_default_time(habit)

    def _get_default_time(self, habit: Habit) -> str:
        """Дефолтное время напоминания на основе типа привычки"""
        name = habit.name.lower()

        # Утренние привычки
        if any(k in name for k in ["витамин", "зарядка", "утр", "завтрак"]):
            return "08:30"

        # Вечерние привычки
        if any(k in name for k in ["спорт", "тренировка", "йога", "прогулка", "вечер"]):
            return "19:00"

        # Привычки с количеством (вода) — напоминаем несколько раз
        if habit.target_value and habit.target_value > 1:
            return "10:00"

        # По умолчанию — утро
        return "09:00"

    async def should_adjust_reminder(self, habit: Habit) -> tuple[bool, str]:
        """
        Проверяет, нужно ли предложить изменить время напоминания.
        Возвращает (нужно_ли, причина).
        """
        # Если игнорировали 3+ раза подряд
        if habit.ignored_count >= 3:
            return True, "Ты часто пропускаешь это напоминание. Может, сменим время?"

        # Если давно не корректировали и есть новые данные
        if habit.last_reminder_adjust:
            days_since = (datetime.now(self.timezone) - habit.last_reminder_adjust).days
            if days_since >= 7:
                patterns = await self.analyze_habit_patterns(habit, days=7)
                if patterns:
                    return True, None  # Тихо обновляем

        return False, None

    async def record_reminder_response(
        self,
        habit: Habit,
        was_acted_on: bool,
        action_delay_minutes: int = 0
    ):
        """
        Записывает реакцию на напоминание для обучения.

        was_acted_on: выполнил ли пользователь привычку после напоминания
        action_delay_minutes: через сколько минут после напоминания выполнил
        """
        if was_acted_on:
            habit.ignored_count = 0

            # Если выполнил значительно позже — учтём это
            if action_delay_minutes > 60:
                # Пользователь выполняет позже, чем напоминаем
                # Нужно сдвинуть время напоминания
                await self._adjust_reminder_later(habit, action_delay_minutes)
        else:
            habit.ignored_count += 1

        await self.session.commit()

    async def _adjust_reminder_later(self, habit: Habit, delay_minutes: int):
        """Сдвигает время напоминания позже на основе реального поведения"""
        current_time = await self.get_reminder_time(habit)
        if not current_time:
            return

        try:
            h, m = map(int, current_time.split(":"))
            current_minutes = h * 60 + m

            # Сдвигаем на половину задержки (компромисс)
            adjustment = min(delay_minutes // 2, 60)  # Максимум на час
            new_minutes = current_minutes + adjustment

            # Округляем до 15 минут
            new_minutes = (new_minutes // 15) * 15

            # Не позже 22:00
            new_minutes = min(new_minutes, 22 * 60)

            new_h = new_minutes // 60
            new_m = new_minutes % 60
            new_time = f"{new_h:02d}:{new_m:02d}"

            # Обновляем выученное время для текущего дня
            weekday = datetime.now(self.timezone).weekday()
            learned = {}
            if habit.learned_times:
                try:
                    learned = json.loads(habit.learned_times)
                except json.JSONDecodeError:
                    pass

            learned[str(weekday)] = [new_time]
            habit.learned_times = json.dumps(learned)

            logger.info(f"Сдвинули напоминание для привычки {habit.id} с {current_time} на {new_time}")

        except ValueError:
            pass

    async def get_habits_to_remind(self, user_id: int) -> list[tuple[Habit, str]]:
        """
        Получает привычки, для которых сейчас нужно отправить напоминание.
        Возвращает список (привычка, время_напоминания).
        """
        now = datetime.now(self.timezone)
        current_time = now.strftime("%H:%M")
        weekday = now.weekday()

        # Получаем активные привычки пользователя с включёнными напоминаниями
        result = await self.session.execute(
            select(Habit).where(
                and_(
                    Habit.user_id == user_id,
                    Habit.is_active == True,
                    Habit.reminder_enabled == True
                )
            )
        )
        habits = result.scalars().all()

        to_remind = []

        for habit in habits:
            # Проверяем, доступен ли этот день
            if habit.reminder_days:
                available_days = habit.reminder_days.split(",")
                if str(weekday) not in available_days:
                    continue

            # Получаем время напоминания
            reminder_time = await self.get_reminder_time(habit, weekday)
            if not reminder_time:
                continue

            # Проверяем, совпадает ли время (с погрешностью 2 минуты)
            try:
                r_h, r_m = map(int, reminder_time.split(":"))
                c_h, c_m = map(int, current_time.split(":"))

                r_minutes = r_h * 60 + r_m
                c_minutes = c_h * 60 + c_m

                if abs(r_minutes - c_minutes) <= 2:
                    to_remind.append((habit, reminder_time))

            except ValueError:
                continue

        return to_remind

    async def generate_adjustment_suggestion(self, habit: Habit) -> Optional[str]:
        """
        Генерирует предложение по изменению времени напоминания.
        """
        patterns = await self.analyze_habit_patterns(habit, days=7)
        if not patterns:
            return None

        weekday = datetime.now(self.timezone).weekday()
        if weekday not in patterns:
            return None

        times = patterns[weekday]
        if len(times) < 2:
            return None

        optimal = self.calculate_optimal_time(times)
        current = await self.get_reminder_time(habit, weekday)

        if optimal == current:
            return None

        return (
            f"Я заметил, что ты обычно выполняешь «{habit.name}» около "
            f"{self._times_to_text(times)}. "
            f"Хочешь, буду напоминать в {optimal}?"
        )

    def _times_to_text(self, times: list[str]) -> str:
        """Форматирует список времён в читаемый текст"""
        if not times:
            return ""
        if len(times) == 1:
            return times[0]

        # Находим диапазон
        minutes = []
        for t in times:
            try:
                h, m = map(int, t.split(":"))
                minutes.append(h * 60 + m)
            except ValueError:
                continue

        if not minutes:
            return times[0]

        avg = sum(minutes) // len(minutes)
        h = avg // 60
        m = avg % 60

        return f"{h:02d}:{m:02d}"
