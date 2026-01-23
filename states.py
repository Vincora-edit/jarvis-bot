from aiogram.fsm.state import State, StatesGroup

class StatesTime(StatesGroup):
    time = State()

class StatesDays(StatesGroup):
    days = State()

class StateTimeForEdit(StatesGroup):
    time = State()

# НОВЫЕ состояния для создания события
class CreateEventStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_datetime = State()
    waiting_for_description = State()
    confirm_event = State()


# Состояние для подтверждения конфликта при создании события
class ConfirmConflictStates(StatesGroup):
    waiting_for_confirmation = State()


# Состояние для дневника
class DiaryStates(StatesGroup):
    writing = State()


# Состояния для утреннего чек-ина
class MorningCheckinStates(StatesGroup):
    waiting_for_sleep = State()
    waiting_for_bedtime = State()
    waiting_for_wakeup = State()
    waiting_for_water = State()
    waiting_for_focus = State()  # Ожидание ответа на "Какая главная задача?"


# Состояния для вечерней рефлексии
class ReflectionStates(StatesGroup):
    writing = State()  # Пользователь пишет рефлексию


# Состояние ожидания времени для события
class WaitingForEventTime(StatesGroup):
    waiting = State()  # Ожидаем время или уточнение


# Состояния для настройки привычки
class HabitSetupStates(StatesGroup):
    waiting_for_days = State()      # Ждём выбор дней недели (для спорта и т.д.)
    waiting_for_time = State()      # Ждём время напоминания
    waiting_for_count = State()     # Ждём количество (стаканов воды, раз витаминов)
    waiting_for_bedtime = State()   # Ждём время сна
    waiting_for_custom_time = State()  # Ждём время для кастомной привычки
    waiting_for_custom_name = State()  # Ждём название кастомной привычки
    waiting_for_interval = State()  # Ждём интервал напоминаний (для воды)


# Состояния для уточнения самочувствия
class MoodStates(StatesGroup):
    waiting_for_reason = State()    # Ждём причину плохого настроения


# Состояния для настройки режима работы бота
class WorkingHoursStates(StatesGroup):
    waiting_for_start_time = State()  # Ждём время начала (07:00, 08:00, и т.д.)
    waiting_for_end_time = State()    # Ждём время конца (20:00, 21:00, и т.д.)


# Состояния для настройки бронирования
class BookingStates(StatesGroup):
    waiting_for_duration = State()      # Ждём выбор длительности
    waiting_for_work_start = State()    # Ждём начало рабочего дня
    waiting_for_work_end = State()      # Ждём конец рабочего дня
    waiting_for_days_ahead = State()    # Ждём выбор периода (на сколько дней вперёд)