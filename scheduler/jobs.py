"""
Планировщик задач (APScheduler).
Все регулярные уведомления.
"""
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import config

logger = logging.getLogger(__name__)

# Глобальный планировщик
scheduler = AsyncIOScheduler(timezone=pytz.timezone(config.TIMEZONE))

# Кэш отправленных напоминаний: {event_id: {minutes: timestamp}}
# Очищается каждый день
_sent_reminders: dict[str, dict[int, float]] = {}

# Кэш отложенных напоминаний (для отправки при наступлении рабочего времени)
# {user_telegram_id: [{"type": "habit"|"calendar"|"system", "message": str, "keyboard": obj|None}]}
_deferred_reminders: dict[int, list] = {}

# Кэш событий календаря: {user_telegram_id: {"events": [...], "updated_at": timestamp}}
# TTL = 5 минут — события обновляются не чаще раза в 5 минут
_calendar_cache: dict[int, dict] = {}
_CALENDAR_CACHE_TTL = 300  # 5 минут в секундах


def get_cached_events(user_id: int, cal, period: str = "today") -> list:
    """Получить события из кэша или запросить из API"""
    import time

    cache_key = f"{user_id}_{period}"
    now = time.time()

    # Проверяем кэш
    if cache_key in _calendar_cache:
        cached = _calendar_cache[cache_key]
        if now - cached["updated_at"] < _CALENDAR_CACHE_TTL:
            return cached["events"]

    # Кэш устарел или не существует — запрашиваем
    try:
        events = cal.get_events(period=period)
        _calendar_cache[cache_key] = {
            "events": events,
            "updated_at": now
        }
        return events
    except Exception as e:
        logger.warning(f"Ошибка получения событий для {user_id}: {e}")
        # Возвращаем старый кэш если есть
        if cache_key in _calendar_cache:
            return _calendar_cache[cache_key]["events"]
        return []


def invalidate_calendar_cache(user_id: int):
    """Инвалидировать кэш пользователя (вызывать при создании/изменении событий)"""
    keys_to_remove = [k for k in _calendar_cache if k.startswith(f"{user_id}_")]
    for key in keys_to_remove:
        del _calendar_cache[key]


def is_within_working_hours(user, now: datetime) -> tuple[bool, str]:
    """
    Проверяет, находится ли текущее время в рабочем режиме пользователя.
    Возвращает (is_active, start_time).
    """
    current_time = now.strftime("%H:%M")
    start = user.morning_time or "08:00"
    end = user.evening_time or "22:00"

    # Сравниваем строки времени (работает для HH:MM формата)
    if start <= current_time <= end:
        return True, start
    return False, start


def defer_reminder(user_telegram_id: int, reminder_type: str, message: str, keyboard=None):
    """Откладывает напоминание до начала рабочего времени пользователя"""
    if user_telegram_id not in _deferred_reminders:
        _deferred_reminders[user_telegram_id] = []

    _deferred_reminders[user_telegram_id].append({
        "type": reminder_type,
        "message": message,
        "keyboard": keyboard
    })
    logger.info(f"📦 Отложено напоминание для {user_telegram_id}: {reminder_type}")


async def get_user_calendar(user):
    """Получить сервис календаря для пользователя (с OAuth если есть)"""
    from services.calendar_service import CalendarService

    if user.calendar_connected and user.google_credentials:
        return CalendarService(user_credentials=user.google_credentials)
    else:
        return CalendarService()


async def daily_plan_job(bot, get_session):
    """Утренний чек-ин (08:00) — умное приветствие с календарём и привычками"""
    from database import async_session
    from database.models import User, Habit
    from sqlalchemy import select, and_, func
    from keyboards import actions
    from services.habit_service import HabitService

    logger.info("🌅 Запуск утреннего чек-ина")

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        now = datetime.now(pytz.timezone(config.TIMEZONE))

        for user in users:
            try:
                # Проверяем режим работы пользователя
                is_active, _ = is_within_working_hours(user, now)
                if not is_active:
                    continue  # Вне рабочего времени — пропускаем

                weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                          "июля", "августа", "сентября", "октября", "ноября", "декабря"]

                weekday = weekdays[now.weekday()]
                date_str = f"{now.day} {months[now.month - 1]}"

                # Начинаем сообщение
                message = f"☀️ **Доброе утро!**\n{weekday}, {date_str}\n"

                # Добавляем информацию о календаре (если подключён)
                if user.calendar_connected and user.google_credentials:
                    try:
                        cal = await get_user_calendar(user)
                        events = get_cached_events(user.telegram_id, cal, "today")

                        if events:
                            # Форматируем все события дня
                            events_text = cal.format_events_list(events, "today")
                            message += f"\n\n{events_text}"
                        else:
                            message += "\n📅 Сегодня календарь свободен"
                    except Exception as e:
                        logger.error(f"Ошибка чтения календаря: {e}")

                # Добавляем информацию о привычках
                habit_service = HabitService(session)
                habits = await habit_service.get_user_habits(user.id)
                if habits:
                    habit_count = len(habits)
                    word = "привычка" if habit_count == 1 else "привычки" if 2 <= habit_count <= 4 else "привычек"
                    message += f"\n\n💪 {habit_count} {word} на сегодня"

                # Проверяем, есть ли у пользователя активная привычка "Сон"
                habit_result = await session.execute(
                    select(Habit).where(
                        and_(
                            Habit.user_id == user.id,
                            Habit.is_active == True,
                            func.lower(Habit.name) == "сон"
                        )
                    )
                )
                sleep_habit = habit_result.scalar_one_or_none()

                if sleep_habit:
                    # Есть привычка "Сон" — спрашиваем как спалось
                    message += "\n\nКак спалось?"
                    await bot.send_message(
                        user.telegram_id,
                        message,
                        parse_mode="Markdown",
                        reply_markup=actions.morning_sleep_keyboard()
                    )
                else:
                    # Нет привычки "Сон" — просто шлём сводку
                    message += "\n\n🚀 Хорошего дня!"
                    await bot.send_message(
                        user.telegram_id,
                        message,
                        parse_mode="Markdown"
                    )

                logger.info(f"✅ Утренний чек-ин отправлен {user.telegram_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка утреннего чек-ина {user.telegram_id}: {e}")


async def evening_reflection_job(bot, get_session):
    """Вечерняя сводка (21:00) — итоги дня + приглашение на рефлексию"""
    from database import async_session
    from database.models import User, Task
    from sqlalchemy import select, and_
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from services.habit_service import HabitService

    logger.info("🌙 Запуск вечерней сводки")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✍️ Записать рефлексию", callback_data="reflection_yes"),
            InlineKeyboardButton(text="🙅 Не сегодня", callback_data="reflection_no"),
        ]
    ])

    now = datetime.now(pytz.timezone(config.TIMEZONE))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                # Проверяем режим работы пользователя
                is_active, _ = is_within_working_hours(user, now)
                if not is_active:
                    continue  # Вне рабочего времени — пропускаем

                message = "🌙 **Вечерняя сводка**\n"

                # Статистика привычек
                habit_service = HabitService(session)
                status = await habit_service.get_today_status(user.id)
                if status["habits"]:
                    completed = status["completed"]
                    total = status["total"]
                    pct = int((completed / total) * 100) if total > 0 else 0
                    message += f"\n💪 Привычки: {completed}/{total} ({pct}%)"

                    # Список невыполненных
                    not_done = [h for h in status["habits"] if not h["done"]]
                    if not_done and len(not_done) <= 3:
                        names = [f"{h['habit'].emoji} {h['habit'].name}" for h in not_done]
                        message += f"\n   └ Осталось: {', '.join(names)}"

                # Статистика задач из календаря
                if user.calendar_connected and user.google_credentials:
                    try:
                        cal = await get_user_calendar(user)
                        events = get_cached_events(user.telegram_id, cal, "today")
                        if events:
                            message += f"\n📅 Событий сегодня: {len(events)}"

                        # Завтрашние события
                        tomorrow_events = get_cached_events(user.telegram_id, cal, "tomorrow")
                        if tomorrow_events:
                            first_tomorrow = None
                            for e in tomorrow_events:
                                start = e.get("start", {})
                                if "dateTime" in start:
                                    start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                                    start_local = start_dt.astimezone(pytz.timezone(config.TIMEZONE))
                                    first_tomorrow = (e.get("summary", "Событие"), start_local.strftime("%H:%M"))
                                    break
                            if first_tomorrow:
                                message += f"\n\n📆 Завтра первое: {first_tomorrow[0]} в {first_tomorrow[1]}"
                    except Exception as e:
                        logger.error(f"Ошибка чтения календаря: {e}")

                # Стрик
                if status.get("stats"):
                    streak = status["stats"].current_streak
                    if streak > 0:
                        message += f"\n\n🔥 Стрик: {streak} дней подряд!"

                message += "\n\nХочешь записать рефлексию?"

                await bot.send_message(
                    user.telegram_id,
                    message,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                logger.info(f"✅ Вечерняя сводка отправлена {user.telegram_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка вечерней сводки {user.telegram_id}: {e}")


async def weekly_plan_job(bot, get_session):
    """Недельный отчёт (воскресенье в 21:00) — полная статистика"""
    from database import async_session
    from database.models import User, HabitLog, Habit
    from sqlalchemy import select, and_, func
    from services.habit_service import HabitService
    from datetime import timedelta

    logger.info("📅 Запуск недельного отчёта")

    now = datetime.now(pytz.timezone(config.TIMEZONE))
    week_start = now - timedelta(days=7)

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                message = "📊 **Недельный отчёт**\n"
                message += f"_{(now - timedelta(days=6)).strftime('%d.%m')} — {now.strftime('%d.%m')}_\n"

                # Статистика привычек за неделю
                habit_service = HabitService(session)
                habits = await habit_service.get_user_habits(user.id)

                if habits:
                    message += "\n💪 Привычки\n"

                    total_progress = 0
                    total_max = 0

                    for habit in habits:
                        # Для привычек с целевым значением (вода 8 стаканов)
                        if habit.target_value:
                            # Суммируем все значения за неделю
                            sum_result = await session.execute(
                                select(func.sum(HabitLog.value)).where(
                                    and_(
                                        HabitLog.habit_id == habit.id,
                                        HabitLog.date >= week_start
                                    )
                                )
                            )
                            actual_sum = sum_result.scalar() or 0
                            target_sum = habit.target_value * 7  # 8 * 7 = 56 стаканов в неделю
                            pct = min(100, int((actual_sum / target_sum) * 100))
                            message += f"{habit.emoji} {habit.name}: {actual_sum}/{target_sum} ({pct}%)\n"
                            total_progress += actual_sum
                            total_max += target_sum
                        else:
                            # Для простых привычек (спорт, витамины) — считаем дни
                            logs_result = await session.execute(
                                select(func.count(HabitLog.id)).where(
                                    and_(
                                        HabitLog.habit_id == habit.id,
                                        HabitLog.date >= week_start
                                    )
                                )
                            )
                            done_count = logs_result.scalar() or 0
                            pct = int((done_count / 7) * 100)
                            message += f"{habit.emoji} {habit.name}: {done_count}/7 ({pct}%)\n"
                            total_progress += done_count
                            total_max += 7

                    # Общий процент
                    if total_max > 0:
                        total_pct = int((total_progress / total_max) * 100)
                        message += f"\nОбщий прогресс: {total_pct}%"

                # Стрик
                status = await habit_service.get_today_status(user.id)
                if status.get("stats"):
                    stats = status["stats"]
                    if stats.current_streak > 0:
                        message += f"\n🔥 Текущий стрик: **{stats.current_streak} дней**"
                    if stats.longest_streak > stats.current_streak:
                        message += f"\n🏆 Рекорд: {stats.longest_streak} дней"
                    message += f"\n⭐ Уровень {stats.level} ({stats.xp} XP)"

                # События из календаря за неделю (если подключён)
                if user.calendar_connected and user.google_credentials:
                    try:
                        cal = await get_user_calendar(user)
                        # Следующая неделя
                        next_week_events = get_cached_events(user.telegram_id, cal, "week")
                        if next_week_events:
                            message += f"\n\n📅 **На следующей неделе:** {len(next_week_events)} событий"

                            # Первые 3 события
                            shown = 0
                            for e in next_week_events[:3]:
                                start = e.get("start", {})
                                if "dateTime" in start:
                                    start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                                    start_local = start_dt.astimezone(pytz.timezone(config.TIMEZONE))
                                    weekdays_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                                    day = weekdays_short[start_local.weekday()]
                                    message += f"\n   {day}: {e.get('summary', 'Событие')} ({start_local.strftime('%H:%M')})"
                                    shown += 1
                            if len(next_week_events) > 3:
                                message += f"\n   ... и ещё {len(next_week_events) - 3}"
                    except Exception as e:
                        logger.error(f"Ошибка чтения календаря: {e}")

                message += "\n\n🚀 Хорошей недели!"

                await bot.send_message(user.telegram_id, message, parse_mode="Markdown")
                logger.info(f"✅ Недельный отчёт отправлен {user.telegram_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка недельного отчёта {user.telegram_id}: {e}")


async def calendar_reminder_job(bot, get_session):
    """Проверка календаря и умные напоминания для каждого пользователя"""
    global _sent_reminders

    from database import async_session
    from database.models import User
    from sqlalchemy import select
    from services.smart_reminder_service import SmartReminderService
    from services.memory_service import MemoryService
    import time

    now = datetime.now(pytz.timezone(config.TIMEZONE))
    reminder_service = SmartReminderService()

    # Очищаем старые записи (старше 24 часов)
    current_ts = time.time()
    for event_id in list(_sent_reminders.keys()):
        for minutes in list(_sent_reminders[event_id].keys()):
            if current_ts - _sent_reminders[event_id][minutes] > 86400:
                del _sent_reminders[event_id][minutes]
        if not _sent_reminders[event_id]:
            del _sent_reminders[event_id]

    try:
        async with async_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()

            # Для каждого пользователя проверяем его ЛИЧНЫЙ календарь
            for user in users:
                try:
                    # ВАЖНО: Напоминания только для пользователей с подключённым личным календарём
                    # Иначе все получают напоминания из общего календаря!
                    if not user.calendar_connected or not user.google_credentials:
                        continue  # Пропускаем — нет личного календаря

                    cal = await get_user_calendar(user)
                    events = get_cached_events(user.telegram_id, cal, "today")

                    for event in events:
                        start = event.get("start", {})
                        if "dateTime" not in start:
                            continue

                        event_id = event.get("id", "")
                        start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                        start_local = start_dt.astimezone(pytz.timezone(config.TIMEZONE))
                        title = event.get("summary", "Событие")

                        # Разница до события в минутах
                        diff = (start_local - now).total_seconds() / 60

                        # Определяем, попадаем ли мы в окно напоминания
                        # Окно: от rt до rt+1 (чтобы не отправлять раньше времени)
                        remind_times = reminder_service.get_reminder_times(title)
                        remind_bucket = None
                        for rt in remind_times:
                            # Окно: rt до rt+1 (например, 60-61 для "за час")
                            # Так мы не отправим напоминание раньше нужного времени
                            if rt <= diff <= rt + 1:
                                remind_bucket = rt
                                break

                        # Если не попали в окно — пропускаем
                        if remind_bucket is None:
                            continue

                        # Уникальный ключ для напоминания (user_id + event_id)
                        reminder_key = f"{user.telegram_id}_{event_id}"

                        # Проверяем, не отправляли ли уже это напоминание
                        if reminder_key in _sent_reminders and remind_bucket in _sent_reminders.get(reminder_key, {}):
                            continue  # Уже отправляли

                        message = reminder_service.generate_reminder(
                            title=title,
                            event_time=start_local,
                            minutes_until=remind_bucket,  # Используем ровное значение (60, 15)
                            include_prep=True
                        )

                        # Проверяем режим работы пользователя
                        is_active, _ = is_within_working_hours(user, now)

                        if is_active:
                            # Рабочее время — отправляем сразу
                            try:
                                await bot.send_message(user.telegram_id, message)
                                # Сохраняем напоминание в историю для контекста (чтобы "удали эту задачу" работало)
                                memory = MemoryService(session)
                                await memory.save_message(
                                    user.id,
                                    "assistant",
                                    f"[Напоминание о событии: {title}] {message}",
                                    "reminder"
                                )
                            except Exception as e:
                                logger.error(f"❌ Ошибка отправки: {e}")
                            logger.info(f"Напоминание ({remind_bucket} мин) для {user.telegram_id}: {title}")
                        else:
                            # Вне рабочего времени — откладываем
                            defer_reminder(user.telegram_id, "calendar", message)

                        # Запоминаем, что обработали
                        if reminder_key not in _sent_reminders:
                            _sent_reminders[reminder_key] = {}
                        _sent_reminders[reminder_key][remind_bucket] = current_ts

                except Exception as e:
                    logger.error(f"❌ Ошибка проверки календаря для {user.telegram_id}: {e}")

    except Exception as e:
        logger.error(f"❌ Общая ошибка проверки календаря: {e}")


# Кэш отправленных напоминаний о привычках: {habit_id_time: timestamp}
_sent_habit_reminders: dict[str, float] = {}


async def personalized_habit_reminder_job(bot, get_session):
    """Персонализированные напоминания о привычках каждую минуту (с адаптивным временем)"""
    global _sent_habit_reminders

    from database import async_session
    from database.models import User, Habit, HabitLog
    from sqlalchemy import select
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from services.smart_habits_service import SmartHabitsService
    import json
    import time

    now = datetime.now(pytz.timezone(config.TIMEZONE))
    current_time = now.strftime("%H:%M")
    current_day = now.weekday()  # 0 = Пн, 6 = Вс
    current_ts = time.time()

    # Очищаем старые записи (старше 24 часов)
    for key in list(_sent_habit_reminders.keys()):
        if current_ts - _sent_habit_reminders[key] > 86400:
            del _sent_habit_reminders[key]

    try:
        async with async_session() as session:
            smart_service = SmartHabitsService(session)

            # Получаем все активные привычки с включёнными напоминаниями
            result = await session.execute(
                select(Habit).where(
                    Habit.is_active == True,
                    Habit.reminder_enabled == True
                )
            )
            habits = result.scalars().all()

            # Логируем для диагностики (каждые 10 минут)
            if now.minute % 10 == 0:
                logger.info(f"🔍 Привычки: найдено {len(habits)} активных с напоминаниями. Текущее время: {current_time}, день: {current_day}")

            for habit in habits:
                try:
                    # Проверяем день недели
                    reminder_days = habit.reminder_days or "0,1,2,3,4,5,6"
                    allowed_days = [int(d) for d in reminder_days.split(",")]
                    if current_day not in allowed_days:
                        continue

                    # Получаем время напоминания (смарт или персональное)
                    # Приоритет: выученное > персональное > дефолтное
                    reminder_time = await smart_service.get_reminder_time(habit, current_day)

                    # Также проверяем персональные времена (для привычек с несколькими напоминаниями)
                    reminder_times = []
                    if reminder_time:
                        reminder_times.append(reminder_time)

                    # Добавляем персональные времена (если есть и отличаются)
                    if habit.reminder_times:
                        try:
                            personal_times = json.loads(habit.reminder_times)
                            for pt in personal_times:
                                if pt not in reminder_times:
                                    reminder_times.append(pt)
                        except json.JSONDecodeError:
                            pass

                    for reminder_time in reminder_times:
                        # Проверяем, совпадает ли текущее время
                        if not _time_matches(current_time, reminder_time):
                            continue

                        logger.info(f"⏰ Совпадение времени для привычки '{habit.name}': {current_time} == {reminder_time}")

                        # Уникальный ключ: habit_id + время + дата
                        reminder_key = f"{habit.id}_{reminder_time}_{now.date()}"

                        # Проверяем, не отправляли ли уже
                        if reminder_key in _sent_habit_reminders:
                            continue

                        # Получаем пользователя
                        user_result = await session.execute(
                            select(User).where(User.id == habit.user_id)
                        )
                        user = user_result.scalar_one_or_none()
                        if not user:
                            continue

                        # Проверяем, не выполнена ли уже привычка сегодня
                        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                        log_result = await session.execute(
                            select(HabitLog).where(
                                HabitLog.habit_id == habit.id,
                                HabitLog.date >= today_start
                            )
                        )
                        existing_log = log_result.scalar_one_or_none()

                        # Для привычек с target_value проверяем достигнута ли цель
                        if habit.target_value:
                            if existing_log and existing_log.value >= habit.target_value:
                                continue  # Цель достигнута
                            current_value = existing_log.value if existing_log else 0
                            progress_text = f" ({current_value}/{habit.target_value})"
                        else:
                            if existing_log:
                                continue  # Уже выполнена
                            progress_text = ""

                        # Формируем сообщение
                        message = _get_habit_reminder_message(habit, progress_text)

                        # Кнопка для отметки
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="✅ Сделал",
                                callback_data=f"habit_done_{habit.id}"
                            )]
                        ])

                        # Проверяем режим работы пользователя
                        is_active, _ = is_within_working_hours(user, now)

                        if is_active:
                            # Рабочее время — отправляем сразу
                            try:
                                await bot.send_message(
                                    user.telegram_id,
                                    message,
                                    reply_markup=keyboard
                                )
                                _sent_habit_reminders[reminder_key] = current_ts
                                logger.info(f"📬 Напоминание о привычке '{habit.name}' отправлено {user.telegram_id}")
                            except Exception as e:
                                logger.error(f"❌ Ошибка отправки напоминания: {e}")
                        else:
                            # Вне рабочего времени — откладываем
                            defer_reminder(user.telegram_id, "habit", message, keyboard)
                            _sent_habit_reminders[reminder_key] = current_ts  # Помечаем как обработанное

                except Exception as e:
                    logger.error(f"❌ Ошибка обработки привычки {habit.id}: {e}")

    except Exception as e:
        logger.error(f"❌ Общая ошибка напоминаний о привычках: {e}")


def _time_matches(current: str, target: str) -> bool:
    """Проверяет точное совпадение времени (час и минута)"""
    try:
        curr_h, curr_m = map(int, current.split(":"))
        targ_h, targ_m = map(int, target.split(":"))
        return curr_h == targ_h and curr_m == targ_m
    except (ValueError, AttributeError) as e:
        logger.debug(f"Ошибка сравнения времени '{current}' vs '{target}': {e}")
        return False


def _get_habit_reminder_message(habit, progress_text: str = "") -> str:
    """Генерация сообщения напоминания в зависимости от типа привычки"""
    name = habit.name.lower()

    if "спорт" in name or "трениров" in name:
        return f"💪 Время тренировки! Погнали?{progress_text}"
    elif "вод" in name:
        return f"💧 Выпей стакан воды{progress_text}"
    elif "витамин" in name:
        return f"💊 Пора выпить витамины{progress_text}"
    elif "зарядк" in name:
        return f"🌅 Утренняя зарядка! 10 минут бодрости{progress_text}"
    elif "сон" in name or "спать" in name:
        return f"🌙 Через час пора спать. Убирай телефон{progress_text}"
    elif "медитац" in name:
        return f"🧘 Время медитации{progress_text}"
    elif "чтен" in name or "книг" in name:
        return f"📚 Время для чтения{progress_text}"
    elif "прогулк" in name:
        return f"🚶 Пора на прогулку{progress_text}"
    else:
        return f"{habit.emoji} {habit.name}{progress_text}"


async def mood_checkin_job(bot, get_session):
    """Опрос самочувствия (утро 09:00, день 14:00, вечер 21:00)"""
    from database import async_session
    from database.models import User
    from sqlalchemy import select
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    now = datetime.now(pytz.timezone(config.TIMEZONE))
    hour = now.hour

    # Определяем время суток для сообщения
    if hour < 12:
        greeting = "🌅 Доброе утро! Как ты себя чувствуешь?"
    elif hour < 18:
        greeting = "☀️ Как настроение в середине дня?"
    else:
        greeting = "🌙 Как прошёл день? Как самочувствие?"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Отлично", callback_data="mood_great"),
         InlineKeyboardButton(text="😊 Хорошо", callback_data="mood_good")],
        [InlineKeyboardButton(text="😐 Нормально", callback_data="mood_ok"),
         InlineKeyboardButton(text="😔 Так себе", callback_data="mood_bad")],
        [InlineKeyboardButton(text="😩 Плохо", callback_data="mood_awful")],
    ])

    logger.info(f"💭 Запуск опроса самочувствия ({hour}:00)")

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                # Проверяем режим работы пользователя
                is_active, _ = is_within_working_hours(user, now)
                if not is_active:
                    continue  # Вне рабочего времени — пропускаем

                await bot.send_message(
                    user.telegram_id,
                    greeting,
                    reply_markup=keyboard
                )
                logger.info(f"💭 Опрос самочувствия отправлен {user.telegram_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки опроса самочувствия {user.telegram_id}: {e}")


async def habits_checkin_job(bot, get_session):
    """Утренний чек-ин привычек (08:30) — только для привычек БЕЗ персональных напоминаний"""
    from database import async_session
    from database.models import User, Habit
    from services.habit_service import HabitService
    from sqlalchemy import select, or_

    logger.info("✅ Запуск утреннего чек-ина привычек")

    now = datetime.now(pytz.timezone(config.TIMEZONE))

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                # Проверяем режим работы пользователя
                is_active, _ = is_within_working_hours(user, now)
                if not is_active:
                    continue  # Вне рабочего времени — пропускаем

                # Проверяем, есть ли у пользователя привычки БЕЗ персональных напоминаний
                habits_result = await session.execute(
                    select(Habit).where(
                        Habit.user_id == user.id,
                        Habit.is_active == True,
                        or_(
                            Habit.reminder_times.is_(None),
                            Habit.reminder_enabled == False
                        )
                    )
                )
                habits_without_reminders = habits_result.scalars().all()

                # Если все привычки имеют персональные напоминания — пропускаем чек-ин
                if not habits_without_reminders:
                    continue

                habit_service = HabitService(session)
                status = await habit_service.get_today_status(user.id)

                if status["habits"]:
                    message = habit_service.format_habits_message(status)
                    message += "\n\n💡 Отмечай привычки: /habit_done [номер]"
                    await bot.send_message(user.telegram_id, message, parse_mode="Markdown")
                    logger.info(f"✅ Чек-ин привычек отправлен {user.telegram_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка чек-ина привычек {user.telegram_id}: {e}")


async def focus_check_job(bot, get_session):
    """Напоминание о фокусе (каждые 3 часа) — персонализированное для каждого пользователя"""
    from database import async_session
    from database.models import User
    from sqlalchemy import select
    import random

    logger.info("🎯 Запуск фокус-чека")

    now = datetime.now(pytz.timezone(config.TIMEZONE))
    hour = now.hour

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                # Получаем календарь пользователя (только если подключён личный)
                events = []
                if user.calendar_connected and user.google_credentials:
                    cal = await get_user_calendar(user)
                    events = get_cached_events(user.telegram_id, cal, "today")

                # Ищем ближайшее событие в пределах 2 часов
                next_event = None
                minutes_until = None
                for e in events:
                    start = e.get("start", {})
                    if "dateTime" in start:
                        start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                        start_local = start_dt.astimezone(pytz.timezone(config.TIMEZONE))
                        if start_local > now:
                            diff_minutes = (start_local - now).total_seconds() / 60
                            # Только если событие в пределах 2 часов (120 минут)
                            if diff_minutes <= 120:
                                next_event = e
                                minutes_until = int(diff_minutes)
                            break

                # Разные сообщения в зависимости от времени и контекста
                if next_event and minutes_until is not None:
                    title = next_event.get("summary", "событие")
                    # Форматируем время до события
                    if minutes_until >= 60:
                        hours = minutes_until // 60
                        mins = minutes_until % 60
                        time_str = f"{hours} ч {mins} мин" if mins > 0 else f"{hours} час"
                    else:
                        time_str = f"{minutes_until} мин"

                    messages = [
                        f"🎯 Через {time_str} — {title}",
                        f"🎯 Напоминаю: {title} через {time_str}",
                    ]
                elif hour < 12:
                    messages = [
                        "🎯 Фокус-чек: что главное на сегодня?",
                        "🎯 Фокус-чек: какой план на утро?",
                    ]
                elif hour < 17:
                    messages = [
                        "🎯 Фокус-чек: как продвигается?",
                        "🎯 Фокус-чек: над чем работаешь?",
                    ]
                else:
                    messages = [
                        "🎯 Фокус-чек: что успел сегодня?",
                        "🎯 Фокус-чек: как прошёл день?",
                    ]

                message = random.choice(messages)
                await bot.send_message(user.telegram_id, message)

            except Exception as e:
                logger.error(f"❌ Ошибка фокус-чека {user.telegram_id}: {e}")


async def send_deferred_reminders_job(bot, get_session):
    """Отправляет накопленные напоминания при наступлении рабочего времени"""
    global _deferred_reminders

    from database import async_session
    from database.models import User
    from sqlalchemy import select

    now = datetime.now(pytz.timezone(config.TIMEZONE))

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            # Проверяем, наступило ли рабочее время
            is_active, start_time = is_within_working_hours(user, now)
            current_time = now.strftime("%H:%M")

            # Отправляем только если сейчас ровно время начала режима (±1 мин)
            if not is_active or current_time != start_time:
                continue

            # Есть ли отложенные напоминания для этого пользователя?
            user_reminders = _deferred_reminders.get(user.telegram_id, [])
            if not user_reminders:
                continue

            try:
                # Отправляем все накопленные напоминания
                if len(user_reminders) == 1:
                    # Одно напоминание — отправляем как есть
                    reminder = user_reminders[0]
                    await bot.send_message(
                        user.telegram_id,
                        reminder["message"],
                        reply_markup=reminder.get("keyboard")
                    )
                else:
                    # Несколько напоминаний — объединяем в одно сообщение
                    messages = [f"📬 Пропущенные напоминания ({len(user_reminders)}):"]
                    for r in user_reminders:
                        messages.append(f"\n• {r['message']}")
                    await bot.send_message(user.telegram_id, "\n".join(messages))

                logger.info(f"✅ Отправлено {len(user_reminders)} отложенных напоминаний для {user.telegram_id}")

                # Очищаем отправленные
                _deferred_reminders[user.telegram_id] = []

            except Exception as e:
                logger.error(f"❌ Ошибка отправки отложенных напоминаний {user.telegram_id}: {e}")


async def user_reminders_job(bot, get_session):
    """Проверка и отправка пользовательских напоминаний (установленных через 'напомни через X')"""
    from database import async_session
    from database.models import User, Reminder
    from sqlalchemy import select, and_

    now = datetime.now(pytz.timezone(config.TIMEZONE))

    try:
        async with async_session() as session:
            # Получаем все не отправленные напоминания, время которых наступило
            result = await session.execute(
                select(Reminder).where(
                    and_(
                        Reminder.is_sent == False,
                        Reminder.remind_at <= now
                    )
                )
            )
            reminders = result.scalars().all()

            for reminder in reminders:
                try:
                    # Получаем пользователя
                    user_result = await session.execute(
                        select(User).where(User.id == reminder.user_id)
                    )
                    user = user_result.scalar_one_or_none()

                    if not user:
                        continue

                    # Проверяем режим работы пользователя
                    is_active, _ = is_within_working_hours(user, now)

                    message = f"⏰ Напоминание: {reminder.message}"

                    if is_active:
                        # Рабочее время — отправляем сразу
                        await bot.send_message(user.telegram_id, message)
                        reminder.is_sent = True
                        logger.info(f"⏰ Напоминание отправлено {user.telegram_id}: {reminder.message}")
                    else:
                        # Вне рабочего времени — откладываем
                        defer_reminder(user.telegram_id, "system", message)
                        reminder.is_sent = True  # Помечаем как обработанное
                        logger.info(f"⏰ Напоминание отложено для {user.telegram_id}: {reminder.message}")

                except Exception as e:
                    logger.error(f"❌ Ошибка отправки напоминания {reminder.id}: {e}")

            await session.commit()

    except Exception as e:
        logger.error(f"❌ Общая ошибка проверки напоминаний: {e}")


def setup_scheduler(bot, get_session):
    """Настройка всех запланированных задач"""

    # Проверка календаря каждую минуту для напоминаний
    scheduler.add_job(
        calendar_reminder_job,
        CronTrigger(minute="*"),  # Каждую минуту
        args=[bot, get_session],
        id="calendar_reminder",
        replace_existing=True,
    )

    # Персональные напоминания о привычках каждую минуту
    scheduler.add_job(
        personalized_habit_reminder_job,
        CronTrigger(minute="*"),  # Каждую минуту
        args=[bot, get_session],
        id="habit_reminders",
        replace_existing=True,
    )

    # Отправка отложенных напоминаний (при наступлении рабочего времени)
    scheduler.add_job(
        send_deferred_reminders_job,
        CronTrigger(minute="*"),  # Каждую минуту проверяем
        args=[bot, get_session],
        id="deferred_reminders",
        replace_existing=True,
    )

    # Пользовательские напоминания ("напомни через час")
    scheduler.add_job(
        user_reminders_job,
        CronTrigger(minute="*"),  # Каждую минуту проверяем
        args=[bot, get_session],
        id="user_reminders",
        replace_existing=True,
    )

    # Утренний план дня
    scheduler.add_job(
        daily_plan_job,
        CronTrigger(hour=config.MORNING_PLAN_HOUR, minute=0),
        args=[bot, get_session],
        id="daily_plan",
        replace_existing=True,
    )

    # Утренний чек-ин привычек (через 30 мин после плана дня)
    scheduler.add_job(
        habits_checkin_job,
        CronTrigger(hour=config.MORNING_PLAN_HOUR, minute=30),
        args=[bot, get_session],
        id="habits_checkin",
        replace_existing=True,
    )

    # Вечерняя рефлексия
    scheduler.add_job(
        evening_reflection_job,
        CronTrigger(hour=config.EVENING_REFLECTION_HOUR, minute=0),
        args=[bot, get_session],
        id="evening_reflection",
        replace_existing=True,
    )

    # Опрос самочувствия отключён (дублирует утренний чек-ин)
    # mood_hours = [9, 14, 21]
    # for hour in mood_hours:
    #     scheduler.add_job(
    #         mood_checkin_job,
    #         CronTrigger(hour=hour, minute=0),
    #         args=[bot, get_session],
    #         id=f"mood_checkin_{hour}",
    #         replace_existing=True,
    #     )

    # План на неделю — воскресенье в 21:00
    scheduler.add_job(
        weekly_plan_job,
        CronTrigger(day_of_week="sun", hour=21, minute=0),
        args=[bot, get_session],
        id="weekly_plan",
        replace_existing=True,
    )

    # Фокус-чеки отключены (сообщения типа "что делаешь", "как дела")
    # for hour in config.FOCUS_CHECK_HOURS:
    #     scheduler.add_job(
    #         focus_check_job,
    #         CronTrigger(hour=hour, minute=0),
    #         args=[bot, get_session],
    #         id=f"focus_check_{hour}",
    #         replace_existing=True,
    #     )

    # Очистка старых данных — раз в неделю (воскресенье в 04:00)
    scheduler.add_job(
        cleanup_old_data_job,
        CronTrigger(day_of_week="sun", hour=4, minute=0),
        args=[],
        id="cleanup_old_data",
        replace_existing=True,
    )

    # Синхронизация VPN подписок — каждый час
    scheduler.add_job(
        vpn_subscription_sync_job,
        CronTrigger(minute=0),  # Каждый час в :00
        args=[],
        id="vpn_subscription_sync",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("✅ Планировщик задач запущен")
    logger.info(f"   🔔 Календарь: каждую минуту")
    logger.info(f"   💪 Привычки: персональные напоминания каждую минуту")
    logger.info(f"   ⏰ Напоминания: каждую минуту")
    logger.info(f"   📅 План дня: {config.MORNING_PLAN_HOUR}:00")
    logger.info(f"   ✅ Чек-ин привычек: {config.MORNING_PLAN_HOUR}:30")
    logger.info(f"   🌙 Рефлексия: {config.EVENING_REFLECTION_HOUR}:00")
    logger.info(f"   🔄 Синхронизация VPN: каждый час")
    logger.info(f"   🧹 Очистка БД: воскресенье 04:00")

    return scheduler


async def cleanup_old_data_job():
    """Ежедневная очистка старых данных"""
    from database import async_session
    from services.cleanup_service import CleanupService

    logger.info("🧹 Запуск очистки старых данных")

    try:
        async with async_session() as session:
            cleanup = CleanupService(session)
            stats = await cleanup.cleanup_all()

            total = sum(stats.values())
            if total > 0:
                logger.info(f"🧹 Очистка завершена: удалено {total} записей")
            else:
                logger.info("🧹 Очистка: нечего удалять")
    except Exception as e:
        logger.error(f"❌ Ошибка очистки данных: {e}")


async def vpn_subscription_sync_job():
    """
    Синхронизация VPN подписок между БД бота и Marzban.
    Запускается каждый час.

    Действия:
    1. Проверяет истекшие подписки в БД бота
    2. Отключает соответствующих пользователей в Marzban
    3. Обновляет expire в Marzban для активных подписок (если отличается)
    """
    from database import async_session
    from database.models import Subscription, TunnelKey, User
    from sqlalchemy import select, and_
    from services.marzban_service import marzban_api

    logger.info("🔄 Запуск синхронизации VPN подписок")

    now = datetime.utcnow()
    disabled_count = 0
    updated_count = 0

    try:
        async with async_session() as session:
            # 1. Находим все истекшие подписки (expires_at < now и status = active)
            expired_result = await session.execute(
                select(Subscription).where(
                    and_(
                        Subscription.status == "active",
                        Subscription.expires_at.isnot(None),
                        Subscription.expires_at < now
                    )
                )
            )
            expired_subs = expired_result.scalars().all()

            for sub in expired_subs:
                try:
                    # Получаем пользователя
                    user_result = await session.execute(
                        select(User).where(User.id == sub.user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if not user:
                        continue

                    # Отключаем в Marzban
                    success, error = await marzban_api.disable_user(user.telegram_id)
                    if success:
                        # Обновляем статус в БД
                        sub.status = "expired"
                        disabled_count += 1
                        logger.info(f"🔒 VPN отключен для user {user.telegram_id} (подписка истекла)")
                    else:
                        logger.error(f"❌ Не удалось отключить VPN для {user.telegram_id}: {error}")

                except Exception as e:
                    logger.error(f"❌ Ошибка обработки истекшей подписки {sub.id}: {e}")

            # 2. Синхронизируем expire для активных подписок
            active_result = await session.execute(
                select(Subscription, TunnelKey, User).join(
                    TunnelKey, TunnelKey.user_id == Subscription.user_id
                ).join(
                    User, User.id == Subscription.user_id
                ).where(
                    and_(
                        Subscription.status == "active",
                        TunnelKey.is_active == True
                    )
                )
            )
            active_rows = active_result.all()

            for sub, key, user in active_rows:
                try:
                    # Получаем данные из Marzban
                    marzban_user, error = await marzban_api.get_user(user.telegram_id)
                    if not marzban_user:
                        continue

                    marzban_expire = marzban_user.get("expire")

                    # Вычисляем ожидаемый expire
                    if sub.expires_at:
                        expected_expire = int(sub.expires_at.timestamp())
                    else:
                        expected_expire = None  # Бессрочная

                    # Если отличается — обновляем
                    if marzban_expire != expected_expire:
                        if expected_expire:
                            days_left = max(1, (sub.expires_at - now).days)
                            success, error = await marzban_api.update_user_expire(user.telegram_id, days_left)
                        else:
                            # Бессрочная — устанавливаем expire=None
                            # Для этого нужно отдельный метод, пока пропускаем
                            continue

                        if success:
                            updated_count += 1
                            logger.info(f"📅 Обновлен expire для {user.telegram_id}")

                except Exception as e:
                    logger.error(f"❌ Ошибка синхронизации подписки {sub.id}: {e}")

            await session.commit()

        if disabled_count > 0 or updated_count > 0:
            logger.info(f"🔄 Синхронизация VPN завершена: отключено {disabled_count}, обновлено {updated_count}")
        else:
            logger.info("🔄 Синхронизация VPN: всё актуально")

    except Exception as e:
        logger.error(f"❌ Общая ошибка синхронизации VPN: {e}")
