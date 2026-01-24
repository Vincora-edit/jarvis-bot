"""
Клавиатуры для модуля VPN/Туннель.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def tunnel_menu_keyboard(
    has_subscription: bool,
    keys_count: int = 0,
    max_keys: int = 0,
    show_trial: bool = False
) -> InlineKeyboardMarkup:
    """Главное меню туннеля"""
    buttons = []

    if has_subscription:
        # Показываем устройства если есть ключи
        if keys_count > 0:
            buttons.append([
                InlineKeyboardButton(
                    text=f"📱 Мои устройства ({keys_count})",
                    callback_data="tunnel:devices"
                )
            ])

        # Кнопка добавления устройства если не достигнут лимит
        if keys_count < max_keys:
            buttons.append([
                InlineKeyboardButton(
                    text="➕ Добавить устройство / Создать ключ",
                    callback_data="tunnel:add_device"
                )
            ])

        # Тарифы
        buttons.append([InlineKeyboardButton(text="💎 Тарифы", callback_data="tunnel:plans")])
    else:
        # Кнопка триала если доступен
        if show_trial:
            buttons.append([
                InlineKeyboardButton(text="🎁 Попробовать 7 дней бесплатно", callback_data="tunnel:trial")
            ])
        buttons.append([
            InlineKeyboardButton(text="⭐ Оформить подписку", callback_data="tunnel:plans")
        ])

    buttons.append([
        InlineKeyboardButton(text="📖 Инструкция", callback_data="tunnel:help")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def plans_keyboard(show_trial: bool = False, current_plan: str = "free", show_back: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа (Джарвис)

    Args:
        show_trial: показывать кнопку триала
        current_plan: текущий план пользователя (показываем только выше)
        show_back: показывать кнопку "Назад" (False для главного меню)
    """
    buttons = []

    # Триал если доступен
    if show_trial:
        buttons.append([
            InlineKeyboardButton(text="🎁 7 дней бесплатно", callback_data="tunnel:trial")
        ])

    # Тарифы Джарвиса (показываем только те, что выше текущего)
    if current_plan in ["free"]:
        buttons.append([
            InlineKeyboardButton(text="📦 Базовый 199₽/мес", callback_data="tunnel:buy:basic"),
        ])
    if current_plan in ["free", "basic"]:
        buttons.append([
            InlineKeyboardButton(text="⭐ Стандарт 399₽/мес", callback_data="tunnel:buy:standard"),
        ])
    if current_plan in ["free", "basic", "standard"]:
        buttons.append([
            InlineKeyboardButton(text="💎 Про 599₽/мес", callback_data="tunnel:buy:pro"),
        ])

    # Промокод + опционально Назад
    if show_back:
        buttons.append([
            InlineKeyboardButton(text="🎁 Промокод", callback_data="tunnel:promo"),
            InlineKeyboardButton(text="◀️ Назад", callback_data="tunnel:menu")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🎁 Ввести промокод", callback_data="tunnel:promo")
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def plan_periods_keyboard(plan: str) -> InlineKeyboardMarkup:
    """Выбор периода подписки"""
    # Цены из services/plans.py (в рублях)
    prices = {
        "basic": {"1": "199₽", "3": "499₽", "12": "1699₽"},
        "standard": {"1": "399₽", "3": "999₽", "12": "3399₽"},
        "pro": {"1": "599₽", "3": "1499₽", "12": "4999₽"},
    }
    p = prices.get(plan, prices["basic"])

    buttons = [
        [InlineKeyboardButton(text=f"1 месяц — {p['1']}", callback_data=f"tunnel:pay:{plan}:1")],
        [InlineKeyboardButton(text=f"3 месяца — {p['3']} (экономия ~17%)", callback_data=f"tunnel:pay:{plan}:3")],
        [InlineKeyboardButton(text=f"12 месяцев — {p['12']} (экономия ~30%)", callback_data=f"tunnel:pay:{plan}:12")],
        [InlineKeyboardButton(text="◀️ К тарифам", callback_data="tunnel:plans")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def devices_keyboard(keys: list, can_add: bool = False) -> InlineKeyboardMarkup:
    """Список устройств с возможностью удаления, переименования и просмотра ключа"""
    buttons = []

    for key in keys:
        buttons.append([
            InlineKeyboardButton(
                text=f"📱 {key.device_name}",
                callback_data=f"tunnel:show_key:{key.id}"
            ),
            InlineKeyboardButton(
                text="✏️",
                callback_data=f"tunnel:rename:{key.id}"
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"tunnel:revoke:{key.id}"
            )
        ])

    # Кнопка добавления если можно
    if can_add:
        buttons.append([
            InlineKeyboardButton(text="➕ Добавить устройство / Создать ключ", callback_data="tunnel:add_device")
        ])

    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="tunnel:menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Кнопка возврата в меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Меню туннеля", callback_data="tunnel:menu")]
    ])


def confirm_revoke_keyboard(key_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления ключа"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"tunnel:revoke_confirm:{key_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="tunnel:devices")
        ]
    ])


def promo_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой промокода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Ввести промокод", callback_data="tunnel:promo")],
        [InlineKeyboardButton(text="◀️ К тарифам", callback_data="tunnel:plans")]
    ])


def renewal_reminder_keyboard(is_trial: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для напоминания о продлении"""
    buttons = [
        [InlineKeyboardButton(text="⭐ Выбрать тариф", callback_data="tunnel:plans")],
        [InlineKeyboardButton(text="🎁 Ввести промокод", callback_data="tunnel:promo")],
    ]

    if not is_trial:
        # Для платных подписок добавляем кнопку продления
        buttons.insert(0, [
            InlineKeyboardButton(text="💳 Продлить подписку", callback_data="tunnel:renew")
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
