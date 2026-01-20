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
                    text="➕ Добавить устройство",
                    callback_data="tunnel:add_device"
                )
            ])

        # Продлить
        buttons.append([InlineKeyboardButton(text="💳 Продлить", callback_data="tunnel:renew")])
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


def plans_keyboard(show_trial: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа (Джарвис)"""
    buttons = []

    # Триал если доступен
    if show_trial:
        buttons.append([
            InlineKeyboardButton(text="🎁 7 дней бесплатно", callback_data="tunnel:trial")
        ])

    # Тарифы Джарвиса
    buttons.extend([
        [
            InlineKeyboardButton(text="📦 Базовый 199₽", callback_data="tunnel:buy:basic:1"),
        ],
        [
            InlineKeyboardButton(text="⭐ Стандарт 399₽", callback_data="tunnel:buy:standard:1"),
        ],
        [
            InlineKeyboardButton(text="💎 Про 799₽", callback_data="tunnel:buy:pro:1"),
        ],
        [
            InlineKeyboardButton(text="🎁 Промокод", callback_data="tunnel:promo"),
            InlineKeyboardButton(text="◀️ Назад", callback_data="tunnel:menu")
        ]
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def plan_periods_keyboard(plan: str) -> InlineKeyboardMarkup:
    """Выбор периода подписки"""
    prices = {
        "basic": {"1": "199₽", "3": "499₽", "12": "1799₽"},
        "standard": {"1": "399₽", "3": "999₽", "12": "3599₽"},
        "pro": {"1": "799₽", "3": "1999₽", "12": "7199₽"},
    }
    p = prices.get(plan, prices["basic"])

    buttons = [
        [InlineKeyboardButton(text=f"1 месяц — {p['1']}", callback_data=f"tunnel:pay:{plan}:1")],
        [InlineKeyboardButton(text=f"3 месяца — {p['3']} (экономия 15%)", callback_data=f"tunnel:pay:{plan}:3")],
        [InlineKeyboardButton(text=f"12 месяцев — {p['12']} (экономия 25%)", callback_data=f"tunnel:pay:{plan}:12")],
        [InlineKeyboardButton(text="◀️ К тарифам", callback_data="tunnel:plans")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def devices_keyboard(keys: list, can_add: bool = False) -> InlineKeyboardMarkup:
    """Список устройств с возможностью удаления и просмотра ключа"""
    buttons = []

    for key in keys:
        buttons.append([
            InlineKeyboardButton(
                text=f"📱 {key.device_name}",
                callback_data=f"tunnel:show_key:{key.id}"
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"tunnel:revoke:{key.id}"
            )
        ])

    # Кнопка добавления если можно
    if can_add:
        buttons.append([
            InlineKeyboardButton(text="➕ Добавить устройство", callback_data="tunnel:add_device")
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
