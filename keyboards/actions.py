from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Главное меню (кнопки внизу экрана)
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 План"), KeyboardButton(text="✅ Привычки")],
            [KeyboardButton(text="🧠 Разгрузка"), KeyboardButton(text="⚙️ Режим")],
            [KeyboardButton(text="📅 Букинг"), KeyboardButton(text="🔒 Туннель")],
            [KeyboardButton(text="💎 Тарифы"), KeyboardButton(text="👥 Рефералы")]
        ],
        resize_keyboard=True
    )

# Кнопки самочувствия (Inline)
def mood_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Отлично", callback_data="mood_great"),
         InlineKeyboardButton(text="😊 Хорошо", callback_data="mood_good")],
        [InlineKeyboardButton(text="😐 Нормально", callback_data="mood_ok"),
         InlineKeyboardButton(text="😔 Так себе", callback_data="mood_bad")],
        [InlineKeyboardButton(text="😩 Плохо", callback_data="mood_awful")],
    ])

# Кнопки финансов (Inline)
def finance_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍔 Еда", callback_data="fin_food"), InlineKeyboardButton(text="🚕 Такси", callback_data="fin_taxi")],
        [InlineKeyboardButton(text="🏠 Быт", callback_data="fin_home"), InlineKeyboardButton(text="🎁 Прочее", callback_data="fin_other")]
    ])


# Кнопки для добавления привычек (скрываем уже добавленные)
def habits_add_keyboard(existing_habits: list = None):
    """Клавиатура для добавления привычек. existing_habits — список имён уже добавленных привычек."""
    if existing_habits is None:
        existing_habits = []

    # Приводим к нижнему регистру для сравнения
    existing_lower = [h.lower() for h in existing_habits]

    all_habits = [
        ("🏃 Спорт", "habit_add_sport", "спорт"),
        ("💧 Вода", "habit_add_water", "вода"),
        ("🧘 Медитация", "habit_add_meditation", "медитация"),
        ("📚 Чтение", "habit_add_reading", "чтение"),
        ("😴 Сон", "habit_add_sleep", "сон"),
        ("💊 Витамины", "habit_add_vitamins", "витамины"),
        ("🚶 Прогулка", "habit_add_walk", "прогулка"),
        ("💪 Зарядка", "habit_add_workout", "зарядка"),
    ]

    # Фильтруем — оставляем только те, которых ещё нет
    available = [h for h in all_habits if h[2] not in existing_lower]

    if not available:
        # Все стандартные привычки уже добавлены, но можно создать свою
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Своя привычка", callback_data="habit_add_custom")]
        ])

    # Формируем клавиатуру по 2 кнопки в ряд
    buttons = []
    row = []
    for text, callback, _ in available:
        row.append(InlineKeyboardButton(text=text, callback_data=callback))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Добавляем кнопку "Своя привычка"
    buttons.append([InlineKeyboardButton(text="✏️ Своя привычка", callback_data="habit_add_custom")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Кнопки для отметки привычек (генерируется динамически)
def habits_checkin_keyboard(habits: list):
    """Клавиатура для отметки привычек"""
    buttons = []
    for habit in habits:
        btn = InlineKeyboardButton(
            text=f"{habit.emoji} {habit.name}",
            callback_data=f"habit_done_{habit.id}"
        )
        buttons.append([btn])

    # Кнопки управления
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить", callback_data="habit_show_add"),
        InlineKeyboardButton(text="⏰ Время", callback_data="habit_show_edit_time"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data="habit_show_delete")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


# Кнопки для редактирования времени привычек
def habits_edit_time_keyboard(habits: list):
    """Клавиатура для выбора привычки для редактирования времени"""
    buttons = []
    for habit in habits:
        btn = InlineKeyboardButton(
            text=f"⏰ {habit.emoji} {habit.name}",
            callback_data=f"habit_edit_time_{habit.id}"
        )
        buttons.append([btn])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="habit_back")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Кнопки для удаления привычек
def habits_delete_keyboard(habits: list):
    """Клавиатура для удаления привычек"""
    buttons = []
    for habit in habits:
        btn = InlineKeyboardButton(
            text=f"🗑 {habit.emoji} {habit.name}",
            callback_data=f"habit_delete_{habit.id}"
        )
        buttons.append([btn])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="habit_back")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Кнопки для утреннего чек-ина — оценка сна
def morning_sleep_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😴 Отлично", callback_data="sleep_great"),
         InlineKeyboardButton(text="😊 Хорошо", callback_data="sleep_good")],
        [InlineKeyboardButton(text="😐 Нормально", callback_data="sleep_ok"),
         InlineKeyboardButton(text="😩 Плохо", callback_data="sleep_bad")],
    ])


# Кнопки для времени сна (во сколько лёг)
def morning_bedtime_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="22:00", callback_data="bed_22"),
         InlineKeyboardButton(text="23:00", callback_data="bed_23"),
         InlineKeyboardButton(text="00:00", callback_data="bed_00")],
        [InlineKeyboardButton(text="01:00", callback_data="bed_01"),
         InlineKeyboardButton(text="02:00", callback_data="bed_02"),
         InlineKeyboardButton(text="Позже", callback_data="bed_late")],
    ])


# Кнопки для времени подъёма (во сколько встал)
def morning_wakeup_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="6:00", callback_data="wake_6"),
         InlineKeyboardButton(text="7:00", callback_data="wake_7"),
         InlineKeyboardButton(text="8:00", callback_data="wake_8")],
        [InlineKeyboardButton(text="9:00", callback_data="wake_9"),
         InlineKeyboardButton(text="10:00", callback_data="wake_10"),
         InlineKeyboardButton(text="Позже", callback_data="wake_late")],
    ])


# Кнопка "выпей воды"
def morning_water_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💧 Выпил воды", callback_data="water_done")],
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="water_skip")],
    ])


# --- НАСТРОЙКА ПРИВЫЧЕК ---

def habit_days_keyboard(selected_days: list = None):
    """Клавиатура выбора дней недели для привычки"""
    if selected_days is None:
        selected_days = []

    days = [
        ("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3),
        ("Пт", 4), ("Сб", 5), ("Вс", 6)
    ]

    buttons = []
    row = []
    for name, day_num in days:
        check = "✅ " if day_num in selected_days else ""
        row.append(InlineKeyboardButton(
            text=f"{check}{name}",
            callback_data=f"hday_{day_num}"
        ))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Кнопки быстрого выбора
    buttons.append([
        InlineKeyboardButton(text="Каждый день", callback_data="hday_all"),
        InlineKeyboardButton(text="Будни", callback_data="hday_weekdays"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✅ Готово", callback_data="hday_done"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="hday_cancel"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def habit_time_keyboard():
    """Клавиатура выбора времени напоминания"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="07:00", callback_data="htime_07:00"),
            InlineKeyboardButton(text="08:00", callback_data="htime_08:00"),
            InlineKeyboardButton(text="09:00", callback_data="htime_09:00"),
        ],
        [
            InlineKeyboardButton(text="12:00", callback_data="htime_12:00"),
            InlineKeyboardButton(text="13:00", callback_data="htime_13:00"),
            InlineKeyboardButton(text="14:00", callback_data="htime_14:00"),
        ],
        [
            InlineKeyboardButton(text="18:00", callback_data="htime_18:00"),
            InlineKeyboardButton(text="19:00", callback_data="htime_19:00"),
            InlineKeyboardButton(text="20:00", callback_data="htime_20:00"),
        ],
        [
            InlineKeyboardButton(text="21:00", callback_data="htime_21:00"),
            InlineKeyboardButton(text="22:00", callback_data="htime_22:00"),
            InlineKeyboardButton(text="Другое", callback_data="htime_custom"),
        ],
    ])


def habit_interval_keyboard():
    """Клавиатура выбора интервала напоминаний (для воды)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Каждые 30 мин", callback_data="hinterval_30"),
            InlineKeyboardButton(text="Каждый час", callback_data="hinterval_60"),
        ],
        [
            InlineKeyboardButton(text="Каждые 2 часа", callback_data="hinterval_120"),
            InlineKeyboardButton(text="Каждые 3 часа", callback_data="hinterval_180"),
        ],
    ])


def habit_count_keyboard(habit_type: str):
    """Клавиатура выбора количества (для витаминов)"""
    if habit_type == "vitamins":
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="1 раз (утром)", callback_data="hcount_1"),
                InlineKeyboardButton(text="2 раза", callback_data="hcount_2"),
            ],
            [
                InlineKeyboardButton(text="3 раза", callback_data="hcount_3"),
            ],
        ])
    return None


def habit_time_of_day_keyboard():
    """Клавиатура выбора времени суток (утро/вечер)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌅 Утром", callback_data="htod_morning"),
            InlineKeyboardButton(text="🌙 Вечером", callback_data="htod_evening"),
        ],
        [
            InlineKeyboardButton(text="🌅🌙 Оба", callback_data="htod_both"),
        ],
    ])


def habit_reminder_button(habit_id: int, habit_name: str, habit_emoji: str):
    """Кнопка для напоминания о привычке"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Сделал", callback_data=f"habit_done_{habit_id}")],
    ])


def connect_calendar_keyboard():
    """Кнопка для подключения календаря"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Подключить Google Calendar", callback_data="connect_calendar")],
    ])
