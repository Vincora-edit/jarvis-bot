"""
Обработчики сообщений пользователя.
"""
import asyncio
import os
import re
import base64
from datetime import datetime, timedelta

from aiogram import types, Router, F
from aiogram.types import InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from config import config
from database import async_session
from services.ai_service import AIService
from services.memory_service import MemoryService
from services.calendar_service import CalendarService
from services.habit_service import HabitService, DEFAULT_HABITS
from services.encryption_service import encryption
from services.limits_service import LimitsService
from services.plans import get_plan_name
from keyboards import actions
from states import StatesTime, StatesDays, StateTimeForEdit, CreateEventStates, ConfirmConflictStates, DiaryStates, MorningCheckinStates, WaitingForEventTime, HabitSetupStates, MoodStates, WorkingHoursStates, BookingStates
import json
import create_bot

# Глобальный сервис календаря (общий, без OAuth)
_default_calendar_service = None


def get_calendar_service():
    """Получить общий сервис календаря (без OAuth пользователя)"""
    global _default_calendar_service
    if _default_calendar_service is None:
        _default_calendar_service = CalendarService()
    return _default_calendar_service


async def get_user_calendar_service(telegram_id: int) -> CalendarService | None:
    """Получить сервис календаря для пользователя (с его OAuth токенами и timezone).
    Возвращает None если у пользователя нет подключённого календаря."""
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(telegram_id)

        if user.calendar_connected and user.google_credentials:
            return CalendarService(
                user_credentials=user.google_credentials,
                user_timezone=user.timezone
            )
        else:
            # НЕ возвращаем общий календарь - это приватность!
            return None

router = Router()


# --- Вспомогательные функции ---

async def process_calendar_actions(actions: list, message: types.Message, state: FSMContext, telegram_id: int) -> list[str]:
    """Обработка списка действий с календарём. Возвращает список ответов."""
    responses = []
    for action in actions:
        intent = action.get("intent")

        if intent == "create_tasks":
            result = await handle_create_task(action, message, state, telegram_id)
            responses.append(result)
        elif intent == "update_task":
            result = await handle_update_task(action, telegram_id)
            responses.append(result)
        elif intent == "delete_task":
            result = await handle_delete_task(action, telegram_id)
            responses.append(result)
        elif intent == "list_tasks":
            result = await handle_list_tasks(action, telegram_id)
            responses.append(result)
        elif intent == "rename_task":
            result = await handle_rename_task(action, telegram_id)
            responses.append(result)
        elif intent == "find_free_slots":
            result = await handle_find_free_slots(action, telegram_id)
            responses.append(result)
        elif intent == "search_events":
            result = await handle_search_events(action, telegram_id)
            responses.append(result)
        elif intent == "set_reminder":
            result = await handle_set_reminder(action, telegram_id)
            responses.append(result)

    return responses


def get_sheets():
    """Подключение к Google Sheets"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(config.GOOGLE_CREDENTIALS_FILE, scope)
        return gspread.authorize(creds).open(config.FINANCE_SHEET_NAME)
    except Exception as e:
        print(f"⚠️ Ошибка подключения к Google Sheets: {e}")
        return None


# --- БАЗОВЫЕ КОМАНДЫ ---

@router.message(Command("start"))
async def command_start(message: types.Message):
    """Обработка команды /start"""
    from services.google_oauth_service import GoogleOAuthService
    from services.referral_service import ReferralService

    # Проверяем реферальный код в параметрах /start ref_XXXXXXXX
    referral_code = None
    if message.text and " " in message.text:
        param = message.text.split(" ", 1)[1]
        if param.startswith("ref_"):
            referral_code = param[4:].upper()

    # Создаём/обновляем пользователя в БД
    async with async_session() as session:
        memory = MemoryService(session)
        user, is_new = await memory.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        calendar_connected = user.calendar_connected

        # Если новый пользователь и есть реферальный код — привязываем
        if is_new and referral_code:
            ref_service = ReferralService(session)
            success, ref_msg = await ref_service.register_referral(user.id, referral_code)
            # Не показываем ошибку, просто логируем
            if success:
                print(f"Referral registered: user {user.id} from code {referral_code}")

        # Уведомление админа о новом пользователе
        if is_new:
            from services.admin_notify_service import get_admin_notify
            admin_notify = get_admin_notify()
            if admin_notify:
                await admin_notify.notify_new_user(
                    telegram_id=message.from_user.id,
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                    referral_code=referral_code
                )

    name = message.from_user.first_name or ""
    greeting = f"Привет{', ' + name if name else ''}!" if name else "Привет!"

    # Основное приветствие
    await message.answer(
        f"{greeting} Я Джарвис — твой личный AI-ассистент.\n\n"
        "Помогу организовать день, поставлю напоминания, отслежу привычки "
        "и поддержу в достижении целей.",
        reply_markup=actions.main_menu()
    )

    # Если новый пользователь — начинаем пошаговый онбординг
    if is_new:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        # Задержка перед следующим сообщением
        await asyncio.sleep(1.5)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Готов", callback_data="onboard_mode_ready"),
                InlineKeyboardButton(text="⏭ Пропустить", callback_data="onboard_mode_skip"),
            ],
        ])

        await message.answer(
            "⏰ Для начала настроим режим — в какое время буду тебе писать.\n\n"
            "Готов?",
            reply_markup=keyboard
        )

    # Для существующих пользователей — не навязываем онбординг


HELP_TEXT = """
🤖 **Джарвис — твой личный AI-ассистент**

━━━━━━━━━━━━━━━━━━━━━━

📅 **КАЛЕНДАРЬ И РАСПИСАНИЕ**

Говори естественным языком:
• «Добавь встречу завтра в 15:00»
• «Созвон с командой в среду в 10»
• «Перенеси встречу на 16:00»
• «Удали созвон с Иваном»
• «Что на сегодня?» / «План на неделю»

🎙 Можно отправить голосовое или переслать сообщение — Джарвис сам разберёт и добавит в календарь.

Команды:
• /connect\\_calendar — подключить Google Calendar
• /today — план на сегодня

━━━━━━━━━━━━━━━━━━━━━━

✅ **ПРИВЫЧКИ**

Персональный трекер с умными напоминаниями.
При добавлении привычки настраиваешь:
— Время напоминаний
— Частоту (для воды — каждый час/2 часа)

Доступные привычки:
🏃 Спорт, 💧 Вода, 🧘 Медитация, 📚 Чтение
😴 Сон, 💊 Витамины, 🚶 Прогулка, 💪 Зарядка

Команды:
• Кнопка «✅ Привычки» — статус и отметки
• /habit\\_add Йога — добавить свою привычку

━━━━━━━━━━━━━━━━━━━━━━

🧠 **РАЗГРУЗКА ГОЛОВЫ**

Кнопка «🧠 Разгрузить голову»:
Отправь голосовое или текст со всем, что крутится в голове.
Джарвис выделит:
— Задачи → предложит добавить в календарь
— Мысли и идеи → сохранит
— Эмоции → запишет в дневник

━━━━━━━━━━━━━━━━━━━━━━

🎙 **ГОЛОСОВЫЕ И ФОТО**

• Отправь голосовое — расшифрую и выполню
• Отправь скриншот — проанализирую содержимое
• Отправь документ — помогу разобраться

━━━━━━━━━━━━━━━━━━━━━━

🔔 **НАПОМИНАНИЯ**

Попроси напомнить о чём угодно:
• «Напомни позвонить маме в 18:00»
• «Напомни через 30 минут проверить почту»
• «Напомни завтра в 9 утра про встречу»

Напоминания работают без календаря — просто скажи или напиши.

━━━━━━━━━━━━━━━━━━━━━━

⏰ **РЕЖИМ РАБОТЫ**

Я пишу только в твоё рабочее время (по умолчанию 08:00–22:00).
Напоминания вне режима откладываются до утра.

• Кнопка «⚙️ Режим» — настроить время работы бота

Автоматические уведомления:
• Утренний план + события из календаря
• Напоминания о привычках в твоё время
• За 60 и 15 мин до событий календаря
• Вечерняя рефлексия

━━━━━━━━━━━━━━━━━━━━━━

📅 **БУКИНГ**

Создай персональную ссылку для записи на встречу.
Люди смогут выбрать удобное время из твоего календаря.

• Кнопка «📅 Букинг» или /booking
• Встречи автоматически добавляются в Google Calendar

━━━━━━━━━━━━━━━━━━━━━━

🔒 **ТУННЕЛЬ (VPN)**

Защищённый доступ к интернету.
Безлимитный трафик, высокая скорость, серверы в Европе.

• Кнопка «🔒 Туннель» или /tunnel

━━━━━━━━━━━━━━━━━━━━━━

💬 **ПРОСТО ОБЩЕНИЕ**

Спроси что угодно:
• Помогу с текстами и идеями
• Отвечу на вопросы
• Помогу спланировать

━━━━━━━━━━━━━━━━━━━━━━

👥 **РЕФЕРАЛЬНАЯ ПРОГРАММА**

Приглашай друзей и получай бонусы!
+14 дней подписки за каждого оплатившего друга.

• /ref — твоя реферальная ссылка и статистика
• /tarif — посмотреть тарифы
"""


@router.message(Command("help"))
async def command_help(message: types.Message):
    """Справка"""
    await message.answer(HELP_TEXT.strip(), parse_mode="Markdown")


async def get_tariff_message(user_id: int, show_back: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует сообщение с тарифами и текущим планом пользователя

    Args:
        user_id: ID пользователя в Telegram
        show_back: показывать кнопку "Назад" (True для VPN контекста)
    """
    from keyboards.tunnel_kb import plans_keyboard

    async with async_session() as session:
        limits_service = LimitsService(session)
        usage_info = await limits_service.get_usage_info(user_id)

    plan = usage_info["plan"]
    plan_name = usage_info["plan_name"]

    # Формируем текст текущего плана
    text = f"💎 *Тарифные планы Джарвиса*\n\n"
    text += f"📊 *Ваш тариф: {plan_name}*\n"

    # Текущие лимиты
    habits = usage_info["habits"]
    ai = usage_info["ai_requests"]
    reminders = usage_info["reminders"]

    if habits["unlimited"]:
        text += "• Привычки: ∞\n"
    else:
        text += f"• Привычки: {habits['used']}/{habits['limit']}\n"

    if ai["unlimited"]:
        text += "• AI сегодня: ∞\n"
    else:
        text += f"• AI сегодня: {ai['used']}/{ai['limit']}\n"

    if reminders["unlimited"]:
        text += "• Напоминания: ∞\n"
    else:
        text += f"• Напоминания: {reminders['used']}/{reminders['limit']}\n"

    vpn = usage_info["vpn_devices"]
    if vpn["available"]:
        text += f"• VPN: {vpn['used']}/{vpn['limit']} устр.\n"
    else:
        text += "• VPN: нет доступа\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━━━\n\n"

    # Показываем все тарифы с подробностями
    if plan == "free":
        text += (
            "📦 *Базовый* — 199₽/мес\n"
            "• Привычки: до 3\n"
            "• Задач в календарь: 20/неделю\n"
            "• Напоминаний: 5/день\n"
            "• AI запросов: 20/день\n"
            "• VPN: 1 устройство\n"
            "• Букинг: 1 ссылка\n"
            "• Статистика привычек\n\n"
            "💰 _499₽/3 мес (-17%) · 1699₽/год (-29%)_\n\n"
        )

    if plan in ["free", "basic"]:
        text += (
            "⭐ *Стандарт* — 399₽/мес\n"
            "• Привычки: до 5\n"
            "• Задач в календарь: 50/неделю\n"
            "• Напоминаний: 10/день\n"
            "• AI запросов: 50/день\n"
            "• VPN: 3 устройства\n"
            "• Букинг: 3 ссылки\n"
            "• Статистика + недельные отчёты\n\n"
            "💰 _999₽/3 мес (-17%) · 3399₽/год (-29%)_\n\n"
        )

    if plan in ["free", "basic", "standard"]:
        text += (
            "💎 *Про* — 599₽/мес\n"
            "• Привычки: безлимит\n"
            "• Задачи в календарь: безлимит\n"
            "• Напоминания: безлимит\n"
            "• AI запросы: безлимит\n"
            "• VPN: 5 устройств\n"
            "• Букинг: безлимит\n"
            "• Полная статистика + AI-советы\n\n"
            "💰 _1499₽/3 мес (-17%) · 4999₽/год (-31%)_\n"
        )

    if plan == "pro":
        text += "✨ У вас максимальный тариф!\n"

    text += "\n🎁 Есть промокод? Нажмите кнопку ниже"

    keyboard = plans_keyboard(current_plan=plan, show_back=show_back)
    return text, keyboard


@router.message(Command("tarif"))
async def command_tarif(message: types.Message):
    """Показать тарифы"""
    text, keyboard = await get_tariff_message(message.from_user.id)
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)


@router.message(F.text == "💎 Тарифы")
async def button_tarif(message: types.Message):
    """Показать тарифы по кнопке"""
    text, keyboard = await get_tariff_message(message.from_user.id)
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)


@router.message(Command("ref"))
async def command_ref(message: types.Message):
    """Реферальная программа"""
    from services.referral_service import ReferralService, REFERRAL_REWARD_DAYS

    async with async_session() as session:
        ref_service = ReferralService(session)

        # Получаем или создаём реферальный код
        code = await ref_service.get_or_create_referral_code(
            (await MemoryService(session).get_or_create_user(message.from_user.id))[0].id
        )

        # Получаем статистику
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)
        stats = await ref_service.get_referral_stats(user.id)

    if not code:
        await message.answer("Не удалось создать реферальный код. Попробуйте позже.")
        return

    # Формируем реферальную ссылку
    bot_username = (await message.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{code}"

    text = (
        "👥 **Реферальная программа**\n\n"
        f"Приглашай друзей и получай **+{REFERRAL_REWARD_DAYS} дней подписки** "
        "за каждого, кто оплатит!\n\n"
        f"🔗 **Твоя ссылка:**\n`{ref_link}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 **Твоя статистика:**\n"
        f"• Перешли по ссылке: {stats.get('total_invited', 0)}\n"
        f"• Оплатили: {stats.get('paid_count', 0)}\n"
        f"• Накоплено бонусных дней: {stats.get('bonus_days', 0)}\n"
    )

    if stats.get('bonus_days', 0) > 0:
        text += "\n💡 Бонусные дни автоматически добавятся к следующей подписке!"

    await message.answer(text, parse_mode="Markdown")


# Фразы для показа возможностей бота
CAPABILITIES_PATTERNS = [
    "что умеешь", "что ты умеешь", "что можешь", "что ты можешь",
    "что делаешь", "что ты делаешь", "как работаешь", "как ты работаешь",
    "помощь", "хелп", "help", "функции", "возможности", "команды",
    "что ты такое", "кто ты", "расскажи о себе", "что это за бот",
]


@router.message(lambda msg: msg.text and any(p in msg.text.lower() for p in CAPABILITIES_PATTERNS))
async def show_capabilities(message: types.Message):
    """Показать возможности бота при вопросах типа 'что умеешь'"""
    await message.answer(HELP_TEXT.strip(), parse_mode="Markdown")


@router.message(Command("connect_calendar"))
async def command_connect_calendar(message: types.Message):
    """Подключить Google Calendar пользователя"""
    from services.google_oauth_service import GoogleOAuthService
    from config import config

    # Проверяем, настроен ли OAuth
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        await message.answer(
            "OAuth не настроен. Используется общий календарь.\n\n"
            "Для настройки индивидуальных календарей нужно:\n"
            "1. Создать OAuth credentials в Google Cloud Console\n"
            "2. Добавить GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET в .env"
        )
        return

    oauth = GoogleOAuthService()
    auth_url = oauth.create_auth_url(message.from_user.id)

    await message.answer(
        "🔗 **Подключение Google Calendar**\n\n"
        f"[Нажми сюда для авторизации]({auth_url})\n\n"
        "После авторизации ты сможешь видеть свой личный календарь.",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


@router.message(Command("disconnect_calendar"))
async def command_disconnect_calendar(message: types.Message):
    """Отключить Google Calendar"""
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        user.google_credentials = None
        user.calendar_connected = False
        await session.commit()

    await message.answer("✅ Календарь отключён. Теперь используется общий.")


@router.callback_query(F.data == "connect_calendar")
async def callback_connect_calendar(call: types.CallbackQuery):
    """Обработка нажатия кнопки подключения календаря"""
    from services.google_oauth_service import GoogleOAuthService
    from config import config

    # Проверяем, настроен ли OAuth
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        await call.message.edit_text(
            "OAuth не настроен. Обратитесь к администратору."
        )
        await call.answer()
        return

    oauth = GoogleOAuthService()
    auth_url = oauth.create_auth_url(call.from_user.id)

    await call.message.edit_text(
        "🔗 **Подключение Google Calendar**\n\n"
        f"[Нажми сюда для авторизации]({auth_url})\n\n"
        "После авторизации ты сможешь видеть свой личный календарь.",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await call.answer()


@router.message(Command("diary"))
async def command_diary(message: types.Message, state: FSMContext):
    """Начать запись в дневник"""
    await state.set_state(DiaryStates.writing)
    await message.answer(
        "Пиши. /cancel — выйти.",
        parse_mode="Markdown"
    )


@router.message(DiaryStates.writing, Command("cancel"))
async def cancel_diary(message: types.Message, state: FSMContext):
    """Отмена записи в дневник"""
    await state.clear()
    await message.answer("📓 Дневник закрыт")


@router.message(DiaryStates.writing)
async def handle_diary_entry(message: types.Message, state: FSMContext):
    """Обработка записи в дневник"""
    text = message.text

    async with async_session() as session:
        ai = AIService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        # Сохраняем запись
        await memory.save_message(user.id, "diary", text)

        # Генерируем ответ
        response = await ai.chat(
            user_id=user.id,
            message=f"[Запись в дневник] {text}",
            system_prompt="""Ты — внимательный слушатель и коуч. Пользователь написал в свой дневник.

Правила ответа:
1. Кратко (2-3 предложения)
2. Признай эмоции если они есть
3. Можешь задать один уточняющий вопрос
4. Не давай советов если не просят
5. Будь тёплым, но не приторным

Пользователь может продолжать писать — это режим дневника.""",
            user_name=message.from_user.first_name or "друг"
        )

        await message.answer(f"📓 {response}", parse_mode="Markdown")


# --- УТРЕННИЙ ЧЕК-ИН ---

@router.message(Command("morning"))
async def command_morning(message: types.Message, state: FSMContext):
    """Ручной запуск утреннего чек-ина"""
    import pytz

    now = datetime.now(pytz.timezone(config.TIMEZONE))
    weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]

    weekday = weekdays[now.weekday()]
    date_str = f"{now.day} {months[now.month - 1]}"

    msg = f"☀️ **Доброе утро!**\n{weekday}, {date_str}\n\n"
    msg += "Как спалось?"

    # Начинаем сбор данных утреннего чек-ина
    await state.set_state(MorningCheckinStates.waiting_for_sleep)
    await state.update_data(morning_checkin={})

    await message.answer(
        msg,
        parse_mode="Markdown",
        reply_markup=actions.morning_sleep_keyboard()
    )


# --- ПРИВЫЧКИ ---

@router.message(F.text == "✅ Привычки")
@router.message(F.text.lower() == "привычки")
@router.message(Command("habits"))
async def command_habits(message: types.Message):
    """Показать статус привычек на сегодня"""
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        # Проверяем, настроен ли режим (не дефолтные значения)
        if user.morning_time == "08:00" and user.evening_time == "21:00":
            # Режим не настроен — просим настроить
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="06:00", callback_data="wh_init_start_06:00"),
                    InlineKeyboardButton(text="07:00", callback_data="wh_init_start_07:00"),
                    InlineKeyboardButton(text="08:00", callback_data="wh_init_start_08:00"),
                ],
                [
                    InlineKeyboardButton(text="09:00", callback_data="wh_init_start_09:00"),
                    InlineKeyboardButton(text="10:00", callback_data="wh_init_start_10:00"),
                    InlineKeyboardButton(text="11:00", callback_data="wh_init_start_11:00"),
                ],
            ])
            await message.answer(
                "⏰ **Сначала настроим режим**\n\n"
                "Во сколько ты обычно просыпаешься?",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            return

        habits = await habit_service.get_user_habits(user.id)

        if not habits:
            # Нет привычек — показываем кнопки для добавления
            await message.answer(
                "📋 **Привычки**\n\nУ тебя пока нет привычек.\nВыбери что отслеживать:",
                parse_mode="Markdown",
                reply_markup=actions.habits_add_keyboard([])
            )
            return

        status = await habit_service.get_today_status(user.id)
        response = habit_service.format_habits_message(status)

        # Показываем статус + кнопки для отметки
        keyboard = actions.habits_checkin_keyboard(habits)
        await message.answer(response, parse_mode="Markdown", reply_markup=keyboard)


@router.message(Command("habit_add"))
async def command_habit_add(message: types.Message, state: FSMContext):
    """Добавить новую привычку: /habit_add Спорт или /habit_add Вода 8 стаканов"""
    args = message.text.replace("/habit_add", "").strip()

    if not args:
        # Показать кнопки для выбора привычек (фильтруем уже добавленные)
        async with async_session() as session:
            habit_service = HabitService(session)
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(message.from_user.id)
            habits = await habit_service.get_user_habits(user.id)
            existing_names = [h.name for h in habits]

        await message.answer(
            "📋 **Добавить привычку**\n\nВыбери или напиши свою: `/habit_add Йога`",
            parse_mode="Markdown",
            reply_markup=actions.habits_add_keyboard(existing_names)
        )
        return

    # Парсим аргументы
    parts = args.split()
    name = parts[0]
    target_value = None
    unit = None

    if len(parts) >= 3:
        try:
            target_value = int(parts[1])
            unit = " ".join(parts[2:])
        except ValueError:
            name = args  # Всё имя

    elif len(parts) == 2:
        try:
            target_value = int(parts[1])
        except ValueError:
            name = args

    # Определяем эмодзи
    emoji_map = {
        "спорт": "🏃", "вода": "💧", "медитация": "🧘",
        "чтение": "📚", "сон": "😴", "витамины": "💊",
        "прогулка": "🚶", "йога": "🧘", "зарядка": "💪",
        "пробежка": "🏃", "бег": "🏃",
    }
    emoji = emoji_map.get(name.lower(), "✅")

    # Проверяем, нет ли уже такой привычки и лимиты
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        limits = LimitsService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        # Проверяем лимит привычек
        can_add, limit_error = await limits.can_add_habit(user.id)
        if not can_add:
            await message.answer(f"⚠️ {limit_error}", parse_mode="Markdown")
            return

        existing = await habit_service.get_user_habits(user.id)
        if any(h.name.lower() == name.lower() and h.is_active for h in existing):
            await message.answer(f"⚠️ Привычка **{name.capitalize()}** уже есть в списке.", parse_mode="Markdown")
            return

    # Сохраняем данные и спрашиваем время напоминания
    await state.update_data(
        custom_habit_name=name.capitalize(),
        custom_habit_emoji=emoji,
        custom_habit_target=target_value,
        custom_habit_unit=unit,
    )

    await message.answer(
        f"{emoji} **{name.capitalize()}**\n\n"
        "Во сколько напоминать?",
        parse_mode="Markdown",
        reply_markup=actions.habit_time_keyboard()
    )
    await state.set_state(HabitSetupStates.waiting_for_custom_time)


@router.message(Command("habit_done"))
async def command_habit_done(message: types.Message):
    """Отметить привычку выполненной: /habit_done 1 или /habit_done Спорт"""
    args = message.text.replace("/habit_done", "").strip()

    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        habits = await habit_service.get_user_habits(user.id)

        if not habits:
            await message.answer("У тебя пока нет привычек. Добавь командой /habit_add")
            return

        if not args:
            # Показать список для отметки
            lines = ["📋 **Отметить привычку**\n"]
            for i, h in enumerate(habits, 1):
                lines.append(f"`/habit_done {i}` — {h.emoji} {h.name}")
            await message.answer("\n".join(lines), parse_mode="Markdown")
            return

        # Ищем привычку по номеру или имени
        habit = None
        value = 1

        # Парсим значение, если есть
        parts = args.split()
        if len(parts) >= 2:
            try:
                value = int(parts[-1])
                args = " ".join(parts[:-1])
            except ValueError:
                pass

        try:
            idx = int(args) - 1
            if 0 <= idx < len(habits):
                habit = habits[idx]
        except ValueError:
            # Ищем по имени
            for h in habits:
                if h.name.lower() == args.lower():
                    habit = h
                    break

        if not habit:
            await message.answer(f"❌ Не нашёл привычку «{args}»")
            return

        # Логируем
        log, xp_earned, new_achievements = await habit_service.log_habit(
            habit_id=habit.id,
            user_id=user.id,
            value=value
        )

        response = f"✅ {habit.emoji} **{habit.name}** — выполнено!"
        if xp_earned > 0:
            response += f"\n+{xp_earned} XP"

        # Ачивки временно отключены
        # for ach_key in new_achievements:
        #     response += f"\n\n{habit_service.format_achievement_message(ach_key)}"

        await message.answer(response, parse_mode="Markdown")


@router.message(Command("habit_delete"))
async def command_habit_delete(message: types.Message):
    """Удалить привычку: /habit_delete 1 или /habit_delete Спорт"""
    args = message.text.replace("/habit_delete", "").strip()

    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        habits = await habit_service.get_user_habits(user.id)

        if not habits:
            await message.answer("У тебя пока нет привычек.")
            return

        if not args:
            # Показать список для удаления
            lines = ["🗑 **Удалить привычку**\n"]
            for i, h in enumerate(habits, 1):
                lines.append(f"`/habit_delete {i}` — {h.emoji} {h.name}")
            await message.answer("\n".join(lines), parse_mode="Markdown")
            return

        # Ищем привычку по номеру или имени
        habit = None

        try:
            idx = int(args) - 1
            if 0 <= idx < len(habits):
                habit = habits[idx]
        except ValueError:
            # Ищем по имени
            for h in habits:
                if h.name.lower() == args.lower():
                    habit = h
                    break

        if not habit:
            await message.answer(f"❌ Не нашёл привычку «{args}»")
            return

        # Удаляем
        success = await habit_service.delete_habit(habit.id, user.id)

        if success:
            await message.answer(f"🗑 {habit.emoji} **{habit.name}** — удалена", parse_mode="Markdown")
        else:
            await message.answer(f"❌ Не удалось удалить привычку")


@router.message(Command("stats"))
async def command_stats(message: types.Message):
    """Показать статистику и достижения"""
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        streak_info = await habit_service.get_streak_info(user.id)
        stats = await habit_service.get_or_create_stats(user.id)

        lines = ["📊 **Твоя статистика**\n"]

        # Уровень и XP
        level = streak_info["level"]
        xp = streak_info["xp"]
        xp_to_next = streak_info["xp_to_next"]
        progress = int(((xp % 100) / 100) * 10)
        progress_bar = "█" * progress + "░" * (10 - progress)

        lines.append(f"⭐ **Уровень {level}**")
        lines.append(f"[{progress_bar}] {xp} XP")
        lines.append(f"До следующего: {xp_to_next} XP\n")

        # Стрики
        lines.append(f"🔥 Текущий стрик: **{streak_info['current']}** дней")
        lines.append(f"🏆 Рекорд: **{streak_info['longest']}** дней\n")

        # Ачивки
        achievements = stats.achievements or {}
        if achievements:
            lines.append("🎖 **Достижения:**")
            from services.habit_service import ACHIEVEMENTS
            for key in achievements:
                ach = ACHIEVEMENTS.get(key, {})
                lines.append(f"{ach.get('emoji', '🏆')} {ach.get('name', key)}")
        else:
            lines.append("🎖 Пока нет достижений")

        await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("history"))
async def command_history(message: types.Message):
    """Показать историю записей: /history или /history mood или /history 7"""
    from database.models import DiaryEntry
    from sqlalchemy import select, desc

    args = message.text.replace("/history", "").strip().lower()

    # Определяем фильтр и количество записей
    entry_filter = None
    limit = 10

    if args:
        if args.isdigit():
            limit = min(int(args), 30)  # Максимум 30 записей
        elif args in ("mood", "настроение", "самочувствие"):
            entry_filter = "mood"
        elif args in ("reason", "причины", "причина"):
            entry_filter = "mood_reason"
        elif args in ("diary", "дневник"):
            entry_filter = "diary"
        elif args in ("reflection", "рефлексия"):
            entry_filter = "reflection"

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        # Строим запрос
        query = select(DiaryEntry).where(DiaryEntry.user_id == user.id)
        if entry_filter:
            query = query.where(DiaryEntry.entry_type == entry_filter)
        query = query.order_by(desc(DiaryEntry.created_at)).limit(limit)

        result = await session.execute(query)
        entries = result.scalars().all()

        if not entries:
            await message.answer(
                "📓 **История записей**\n\n"
                "Пока ничего не записано.\n\n"
                "Используй:\n"
                "• `/history` — все записи\n"
                "• `/history mood` — самочувствие\n"
                "• `/history 20` — последние 20 записей",
                parse_mode="Markdown"
            )
            return

        # Форматируем записи
        type_emoji = {
            "mood": "💭",
            "mood_reason": "💬",
            "diary": "📓",
            "reflection": "🌙",
            "thought": "💡",
        }

        lines = [f"📓 **История записей** (последние {len(entries)})\n"]

        for entry in entries:
            emoji = type_emoji.get(entry.entry_type, "📝")
            date_str = entry.created_at.strftime("%d.%m %H:%M")
            # Расшифровываем содержимое
            decrypted_content = encryption.decrypt(entry.content)
            content = decrypted_content[:100] + "..." if len(decrypted_content) > 100 else decrypted_content
            lines.append(f"{emoji} `{date_str}` — {content}")

        lines.append("\n**Фильтры:** `/history mood` | `/history diary` | `/history 20`")

        await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("mood_stats"))
async def command_mood_stats(message: types.Message):
    """Статистика самочувствия за неделю"""
    from database.models import DiaryEntry
    from sqlalchemy import select, desc
    from datetime import timedelta

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        # Записи за последние 7 дней
        week_ago = datetime.utcnow() - timedelta(days=7)
        result = await session.execute(
            select(DiaryEntry).where(
                DiaryEntry.user_id == user.id,
                DiaryEntry.entry_type == "mood",
                DiaryEntry.created_at >= week_ago
            ).order_by(desc(DiaryEntry.created_at))
        )
        entries = result.scalars().all()

        if not entries:
            await message.answer(
                "💭 **Статистика самочувствия**\n\n"
                "За последнюю неделю нет данных.\n"
                "Отвечай на опросы самочувствия — они приходят в 09:00, 14:00 и 21:00.",
                parse_mode="Markdown"
            )
            return

        # Считаем статистику
        mood_counts = {"Отлично": 0, "Хорошо": 0, "Нормально": 0, "Так себе": 0, "Плохо": 0}
        total_energy = 0

        for entry in entries:
            # Расшифровываем содержимое для анализа
            content = encryption.decrypt(entry.content).lower()
            if "отлично" in content:
                mood_counts["Отлично"] += 1
            elif "хорошо" in content:
                mood_counts["Хорошо"] += 1
            elif "нормально" in content:
                mood_counts["Нормально"] += 1
            elif "так себе" in content:
                mood_counts["Так себе"] += 1
            elif "плохо" in content:
                mood_counts["Плохо"] += 1

            if entry.energy:
                total_energy += entry.energy

        avg_energy = round(total_energy / len(entries), 1) if entries else 0

        lines = ["💭 **Статистика самочувствия за неделю**\n"]
        lines.append(f"Всего записей: {len(entries)}")
        lines.append(f"Средняя оценка: {avg_energy}/10\n")

        lines.append("**Распределение:**")
        for mood, count in mood_counts.items():
            if count > 0:
                bar = "█" * count
                lines.append(f"{mood}: {bar} ({count})")

        # Получаем причины плохого настроения
        reasons_result = await session.execute(
            select(DiaryEntry).where(
                DiaryEntry.user_id == user.id,
                DiaryEntry.entry_type == "mood_reason",
                DiaryEntry.created_at >= week_ago
            ).order_by(desc(DiaryEntry.created_at)).limit(5)
        )
        reasons = reasons_result.scalars().all()

        if reasons:
            lines.append("\n**Причины плохого настроения:**")
            for r in reasons:
                # Расшифровываем содержимое
                decrypted = encryption.decrypt(r.content)
                content = decrypted.replace("Причина плохого настроения", "").strip()
                content = content.split("):")[1].strip() if "):" in content else content
                lines.append(f"• {content[:80]}")

        await message.answer("\n".join(lines), parse_mode="Markdown")


# --- МЕНЮ ---

@router.message(F.text == "📋 План")
async def show_today_plan(message: types.Message):
    """План на сегодня: события + вопрос о фокусе"""
    import pytz

    try:
        cal = await get_user_calendar_service(message.from_user.id)

        # Если календарь не подключён
        if cal is None:
            response = (
                "📋 **План на сегодня**\n\n"
                "Календарь не подключён.\n\n"
                "Подключи Google Calendar, чтобы видеть свои события "
                "и добавлять задачи голосом или текстом."
            )
            await message.answer(
                response,
                parse_mode="Markdown",
                reply_markup=actions.connect_calendar_keyboard()
            )
            return

        events = cal.get_events(period="today", only_future=False)

        if not events:
            response = (
                "📋 **План на сегодня**\n\n"
                "Сегодня пусто — свободный день!\n\n"
                "🎯 **Какая главная задача на сегодня?**\n"
                "Напиши одну цель, на которой сфокусируешься."
            )
        else:
            response = "📋 **План на сегодня**\n\n"

            # Сортировка по времени начала
            def get_sort_key(event):
                start = event.get("start", {})
                if "dateTime" in start:
                    return datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                elif "date" in start:
                    return datetime.fromisoformat(start["date"] + "T00:00:00+00:00")
                return datetime.min.replace(tzinfo=pytz.UTC)

            sorted_events = sorted(events, key=get_sort_key)

            for event in sorted_events:
                start = event.get("start", {})
                end = event.get("end", {})
                title = event.get("summary", "Событие")
                emoji = cal.get_emoji_for_title(title)

                if "dateTime" in start:
                    start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                    start_local = start_dt.astimezone(pytz.timezone(config.TIMEZONE))
                    time_str = start_local.strftime("%H:%M")

                    # Вычисляем время окончания
                    if "dateTime" in end:
                        end_dt = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
                        end_local = end_dt.astimezone(pytz.timezone(config.TIMEZONE))
                        end_str = end_local.strftime("%H:%M")
                        response += f"• {time_str}–{end_str} — {emoji} {title}\n"
                    else:
                        response += f"• {time_str} — {emoji} {title}\n"
                else:
                    response += f"• {emoji} {title} (весь день)\n"

            response += "\n🎯 **Какая главная задача на сегодня?**"

        await message.answer(response, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# --- ОБРАБОТЧИК ПОДТВЕРЖДЕНИЯ КОНФЛИКТА ---

@router.message(ConfirmConflictStates.waiting_for_confirmation)
async def handle_conflict_confirmation(message: types.Message, state: FSMContext):
    """Обработка ответа на вопрос о пересечении событий"""
    # Если прислали не текст (голосовое, фото и т.д.) — сбрасываем состояние и обрабатываем как обычно
    if not message.text:
        await state.clear()
        return  # Пусть другие обработчики (voice, photo) подхватят

    text = message.text.strip().lower()

    # Положительные ответы
    if text in ["да", "yes", "ок", "ok", "добавь", "добавить", "создай", "создать", "ага", "угу", "конечно", "давай", "+", "1"]:
        data = await state.get_data()
        pending_event = data.get("pending_event")

        if pending_event:
            try:
                cal = await get_user_calendar_service(message.from_user.id)
                if cal is None:
                    await message.answer("❌ Календарь не подключён. Используй /connect_calendar")
                    await state.clear()
                    return
                start_datetime = datetime.fromisoformat(pending_event["start_datetime"])

                # Создаём событие
                cal.create_event(
                    title=pending_event["title"],
                    start_datetime=start_datetime,
                    duration_minutes=pending_event["duration"],
                )

                # Форматируем ответ
                weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                          "июля", "августа", "сентября", "октября", "ноября", "декабря"]

                end_datetime = start_datetime + timedelta(minutes=pending_event["duration"])
                weekday = weekdays[start_datetime.weekday()]
                now = datetime.now(cal.timezone)

                if start_datetime.date() == now.date():
                    date_label = f"Сегодня ({weekday})"
                elif start_datetime.date() == (now + timedelta(days=1)).date():
                    date_label = f"Завтра ({weekday})"
                else:
                    date_label = f"({weekday}) {start_datetime.day} {months[start_datetime.month - 1]}"

                time_start = start_datetime.strftime("%H:%M")
                time_end = end_datetime.strftime("%H:%M")
                emoji = cal.get_emoji_for_title(pending_event["title"])

                response = f"✅ {emoji} [{pending_event['title']}] Добавлен\n"
                response += f" · Дата: {date_label}\n"
                response += f" · Время: {time_start} - {time_end}\n"
                response += f" · Напоминание: За 1 час до; За 15 минут до"

                await message.answer(response, parse_mode="Markdown")
            except Exception as e:
                await message.answer(f"❌ Ошибка создания события: {str(e)[:50]}")
        else:
            await message.answer("❌ Данные события не найдены")

        await state.clear()

    # Отрицательные ответы
    elif text in ["нет", "no", "отмена", "отменить", "не надо", "не нужно", "стоп", "-", "0"]:
        await state.clear()
        await message.answer("🚫 Событие не добавлено")

    # Непонятный ответ
    else:
        await message.answer("❓ Не понял. Напиши **да** чтобы добавить или **нет** чтобы отменить.", parse_mode="Markdown")


@router.message(WaitingForEventTime.waiting)
async def handle_waiting_for_event_time(message: types.Message, state: FSMContext):
    """Обработка сообщения при ожидании времени для события"""
    # Если прислали не текст — сбрасываем состояние
    if not message.text:
        await state.clear()
        return

    text = message.text.strip()
    data = await state.get_data()
    pending_event = data.get("pending_event")

    if not pending_event:
        await state.clear()
        # Передаём сообщение обычному обработчику
        return

    # Получаем сохранённые данные
    title = pending_event.get("title", "Задача")
    date_str = pending_event.get("date")
    duration = pending_event.get("duration", 60)

    # Пробуем распарсить время из ответа пользователя
    time_match = re.search(r'(\d{1,2})[:\.]?(\d{2})?', text)

    if time_match:
        # Нашли время — создаём событие
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0
        time_str = f"{hour:02d}:{minute:02d}"

        await state.clear()

        # Создаём событие
        response = await handle_create_task(
            action={
                "title": title,
                "date": date_str,
                "time": time_str,
                "duration_minutes": duration,
            },
            message=message,
            state=state,
            telegram_id=message.from_user.id
        )
        await message.answer(response, parse_mode="Markdown")
        return

    # Проверяем, хочет ли пользователь изменить название (например "назови задачей")
    text_lower = text.lower()
    rename_patterns = ["назови", "переименуй", "поставь как", "сделай", "не созвоном", "не встречей", "задачей", "напоминанием"]

    if any(p in text_lower for p in rename_patterns):
        # Пытаемся извлечь новое название из текста
        # Например: "поставь не созвоном а задачей" -> изменяем тип
        # "назови Работа над проектом" -> новое название

        # Простые замены типов
        new_title = title
        if "задач" in text_lower:
            # Убираем слова типа "созвон", "встреча" из названия если есть
            new_title = re.sub(r'^(созвон|встреча|звонок)\s*(по\s+)?', '', title, flags=re.IGNORECASE).strip()
            if not new_title:
                new_title = title  # Если осталось пустое — оставляем как было

        # Обновляем pending_event с новым названием
        pending_event["title"] = new_title
        await state.update_data(pending_event=pending_event)

        # Спрашиваем время с обновлённым названием
        if date_str:
            await message.answer(f"✅ Хорошо! **{new_title}** на {date_str} — во сколько?", parse_mode="Markdown")
        else:
            await message.answer(f"✅ Хорошо! **{new_title}** — когда запланировать?", parse_mode="Markdown")
        return

    # Проверяем отмену
    if text_lower in ["отмена", "отменить", "стоп", "не надо", "нет"]:
        await state.clear()
        await message.answer("🚫 Создание события отменено")
        return

    # Если ничего не распознали — напоминаем что ждём время
    if date_str:
        await message.answer(f"⏰ Напиши время для **{title}** на {date_str} (например, 14:00)", parse_mode="Markdown")
    else:
        await message.answer(f"⏰ Напиши дату и время для **{title}** (например, завтра в 14:00)", parse_mode="Markdown")


@router.message(F.text == "💭 Самочувствие")
async def show_mood(message: types.Message):
    """Быстрая запись самочувствия"""
    await message.answer(
        "💭 **Как ты себя чувствуешь?**",
        parse_mode="Markdown",
        reply_markup=actions.mood_keyboard()
    )


@router.message(F.text == "🧠 Разгрузка")
async def brain_dump(message: types.Message):
    await message.answer(
        "🧠 **Разгрузка головы**\n\n"
        "Отправь голосовое или напиши всё, что крутится в голове.\n"
        "Я выделю задачи, мысли и эмоции.",
        parse_mode="Markdown"
    )


@router.message(F.text == "📅 Букинг")
async def booking_button(message: types.Message, state: FSMContext):
    """Кнопка бронирования — вызывает /booking"""
    await command_booking(message, state)


@router.message(F.text == "👥 Рефералы")
async def referrals_button(message: types.Message):
    """Кнопка рефералов — вызывает /ref"""
    await command_ref(message)


@router.message(F.text == "⚙️ Режим")
async def my_schedule_button(message: types.Message):
    """Настройка режима работы бота (кнопка меню)"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        start_time = user.morning_time or "08:00"
        end_time = user.evening_time or "22:00"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌅 Подъём", callback_data="wh_change_start"),
            InlineKeyboardButton(text="🌙 Отбой", callback_data="wh_change_end"),
        ]
    ])

    await message.answer(
        f"⏰ **Твой режим**\n\n"
        f"🌅 Просыпаешься: **{start_time}**\n"
        f"🌙 Ложишься: **{end_time}**\n\n"
        f"Напоминания приходят в это время.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.message(F.text == "🚀 Главное меню")
async def back_to_main(message: types.Message):
    await message.answer("Главное меню:", reply_markup=actions.main_menu())


# --- CALLBACK (INLINE КНОПКИ) ---

@router.callback_query(F.data.startswith("mood_"))
async def mood_callback(call: types.CallbackQuery, state: FSMContext):
    """Запись самочувствия"""
    mood = call.data.replace("mood_", "")
    mood_map = {
        "great": ("🚀 Отлично", 10),
        "good": ("😊 Хорошо", 7),
        "ok": ("😐 Нормально", 5),
        "bad": ("😔 Так себе", 3),
        "awful": ("😩 Плохо", 1),
    }
    mood_text, mood_level = mood_map.get(mood, ("", 5))

    # Запись в Google Sheets
    sh = get_sheets()
    if sh:
        try:
            sh.worksheet("Habits").append_row([str(datetime.now()), "Самочувствие", mood_text])
        except:
            pass

    # Запись в БД
    async with async_session() as session:
        from database.models import DiaryEntry
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        entry = DiaryEntry(
            user_id=user.id,
            content=encryption.encrypt(f"Самочувствие: {mood_text}"),
            energy=mood_level,
            entry_type="mood"
        )
        session.add(entry)
        await session.commit()

    # Если плохое настроение — спрашиваем причину
    if mood in ("bad", "awful"):
        await state.set_state(MoodStates.waiting_for_reason)
        await state.update_data(mood_text=mood_text, mood_level=mood_level)
        await call.message.edit_text(
            f"✅ Записано: {mood_text}\n\n"
            "💬 Что случилось? Напиши пару слов — это поможет отследить причины.\n"
            "_(или отправь /skip чтобы пропустить)_",
            parse_mode="Markdown"
        )
    else:
        await call.message.edit_text(f"✅ Записано: {mood_text}")

    await call.answer()


@router.callback_query(F.data.startswith("fin_"))
async def finance_callback(call: types.CallbackQuery, state: FSMContext):
    """Выбор категории финансов"""
    category = call.data.split("_")[1]
    await state.update_data(fin_category=category)
    await call.message.answer(f"Принято: {category}. Введи сумму:")
    await call.answer()


# --- ОБРАБОТКА ПРИЧИНЫ ПЛОХОГО НАСТРОЕНИЯ ---

@router.message(Command("skip"), MoodStates.waiting_for_reason)
async def skip_mood_reason(message: types.Message, state: FSMContext):
    """Пропуск указания причины плохого настроения"""
    await state.clear()
    await message.answer("Хорошо, пропускаем. Надеюсь, станет лучше! 💙")


@router.message(MoodStates.waiting_for_reason)
async def mood_reason_handler(message: types.Message, state: FSMContext):
    """Обработка причины плохого настроения"""
    reason = message.text.strip()
    data = await state.get_data()
    mood_text = data.get("mood_text", "")

    # Сохраняем причину в БД
    async with async_session() as session:
        from database.models import DiaryEntry
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        entry = DiaryEntry(
            user_id=user.id,
            content=encryption.encrypt(f"Причина плохого настроения ({mood_text}): {reason}"),
            entry_type="mood_reason"
        )
        session.add(entry)
        await session.commit()

    await state.clear()
    await message.answer(
        "💙 Спасибо, что поделился. Записал.\n"
        "Иногда просто выговориться — уже помогает."
    )


# --- CALLBACKS ДЛЯ ПРИВЫЧЕК ---

# Маппинг кнопок к параметрам привычек
HABIT_PRESETS = {
    # setup_type: "days_time" (спрашиваем дни + время), "count" (кол-во), "bedtime" (время сна), "time_of_day" (утро/вечер), "auto" (без вопросов), "interval" (интервал напоминаний)
    "sport": {"name": "Спорт", "emoji": "🏃", "target_value": None, "unit": None, "setup_type": "days_time", "question": "Какие дни тренируемся?"},
    "water": {"name": "Вода", "emoji": "💧", "target_value": None, "unit": None, "setup_type": "interval", "question": "Как часто напоминать пить воду?"},
    "meditation": {"name": "Медитация", "emoji": "🧘", "target_value": None, "unit": None, "setup_type": "time_of_day", "question": "Когда медитируем?"},
    "reading": {"name": "Чтение", "emoji": "📚", "target_value": None, "unit": None, "setup_type": "time_of_day", "question": "Когда читаем?"},
    "sleep": {"name": "Сон", "emoji": "😴", "target_value": 8, "unit": "часов", "setup_type": "bedtime", "question": "Во сколько обычно ложишься спать?"},
    "vitamins": {"name": "Витамины", "emoji": "💊", "target_value": None, "unit": None, "setup_type": "count", "question": "Сколько раз в день пьёшь витамины?"},
    "walk": {"name": "Прогулка", "emoji": "🚶", "target_value": None, "unit": None, "setup_type": "time_of_day", "question": "Когда гуляем?"},
    "workout": {"name": "Зарядка", "emoji": "💪", "target_value": None, "unit": None, "setup_type": "auto", "default_time": "07:00"},
}


@router.callback_query(F.data == "habit_add_custom")
async def habit_add_custom_callback(call: types.CallbackQuery, state: FSMContext):
    """Создание кастомной привычки — запрашиваем название"""
    await call.message.edit_text(
        "✏️ **Своя привычка**\n\n"
        "Напиши название привычки (например: Английский, Растяжка, Дневник):",
        parse_mode="Markdown"
    )
    await state.set_state(HabitSetupStates.waiting_for_custom_name)
    await call.answer()


@router.message(HabitSetupStates.waiting_for_custom_name)
async def habit_custom_name_handler(message: types.Message, state: FSMContext):
    """Получили название кастомной привычки — спрашиваем время"""
    habit_name = message.text.strip()

    if len(habit_name) > 50:
        await message.answer("❌ Слишком длинное название. Максимум 50 символов.")
        return

    if len(habit_name) < 2:
        await message.answer("❌ Слишком короткое название. Минимум 2 символа.")
        return

    # Сохраняем название
    await state.update_data(
        custom_habit_name=habit_name,
        custom_habit_emoji="⭐",
        custom_habit_target=None,
        custom_habit_unit=None,
        habit_key="custom",
        habit_preset={
            "name": habit_name,
            "emoji": "⭐",
            "target_value": None,
            "unit": None,
            "setup_type": "custom"
        }
    )

    # Спрашиваем время напоминания
    await message.answer(
        f"⭐ **{habit_name}**\n\n"
        "Во сколько напоминать?",
        parse_mode="Markdown",
        reply_markup=actions.habit_time_keyboard()
    )
    await state.set_state(HabitSetupStates.waiting_for_custom_time)


@router.callback_query(F.data.startswith("habit_add_"))
async def habit_add_callback(call: types.CallbackQuery, state: FSMContext):
    """Начало настройки привычки — запуск FSM"""
    habit_key = call.data.replace("habit_add_", "")
    preset = HABIT_PRESETS.get(habit_key)

    if not preset:
        await call.answer("Неизвестная привычка")
        return

    # Проверяем лимиты и нет ли уже такой привычки
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)
        habit_service = HabitService(session)
        limits = LimitsService(session)

        # Проверяем лимит привычек
        can_add, limit_error = await limits.can_add_habit(user.id)
        if not can_add:
            await call.message.edit_text(f"⚠️ {limit_error}", parse_mode="Markdown")
            await call.answer()
            return

        existing = await habit_service.get_user_habits(user.id)
        if any(h.name == preset["name"] and h.is_active for h in existing):
            await call.message.edit_text(
                f"⚠️ Привычка **{preset['name']}** уже есть в списке.",
                parse_mode="Markdown"
            )
            await call.answer()
            return

    # Сохраняем данные привычки в FSM
    await state.update_data(
        habit_key=habit_key,
        habit_preset=preset,
        selected_days=[],  # Для выбора дней недели
    )

    setup_type = preset.get("setup_type", "auto")

    if setup_type == "days_time":
        # Спрашиваем дни недели
        await call.message.edit_text(
            f"{preset['emoji']} **{preset['name']}**\n\n{preset['question']}",
            parse_mode="Markdown",
            reply_markup=actions.habit_days_keyboard([])
        )
        await state.set_state(HabitSetupStates.waiting_for_days)

    elif setup_type == "count":
        # Спрашиваем количество
        await call.message.edit_text(
            f"{preset['emoji']} **{preset['name']}**\n\n{preset['question']}",
            parse_mode="Markdown",
            reply_markup=actions.habit_count_keyboard(habit_key)
        )
        await state.set_state(HabitSetupStates.waiting_for_count)

    elif setup_type == "interval":
        # Спрашиваем интервал напоминаний (для воды)
        await call.message.edit_text(
            f"{preset['emoji']} **{preset['name']}**\n\n{preset['question']}",
            parse_mode="Markdown",
            reply_markup=actions.habit_interval_keyboard()
        )
        await state.set_state(HabitSetupStates.waiting_for_interval)

    elif setup_type == "bedtime":
        # Берём время из режима пользователя (за час до конца)
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(call.from_user.id)
            evening_time = user.evening_time or "22:00"

        # Напоминание за час до конца режима
        hour, minute = map(int, evening_time.split(":"))
        reminder_hour = hour - 1 if hour > 0 else 23
        reminder_time = f"{reminder_hour:02d}:{minute:02d}"

        await _create_habit_with_schedule(
            call.from_user.id,
            preset,
            reminder_times=[reminder_time],
            reminder_days="0,1,2,3,4,5,6"
        )

        await call.message.edit_text(
            f"✅ Привычка добавлена!\n\n"
            f"{preset['emoji']} **{preset['name']}**\n"
            f"📅 Напоминание: каждый день в {reminder_time}\n\n"
            f"💡 Время взято из твоего режима (за час до {evening_time})",
            parse_mode="Markdown"
        )
        await state.clear()

    elif setup_type == "time_of_day":
        # Спрашиваем утро/вечер
        await call.message.edit_text(
            f"{preset['emoji']} **{preset['name']}**\n\n{preset['question']}",
            parse_mode="Markdown",
            reply_markup=actions.habit_time_of_day_keyboard()
        )
        await state.set_state(HabitSetupStates.waiting_for_time)

    else:  # auto — создаём с временем из режима (утренние привычки)
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(call.from_user.id)
            morning_time = user.morning_time or "08:00"

        await _create_habit_with_schedule(
            call.from_user.id,
            preset,
            reminder_times=[morning_time],
            reminder_days="0,1,2,3,4,5,6"
        )
        await call.message.edit_text(
            f"✅ Привычка добавлена!\n\n{preset['emoji']} **{preset['name']}**\n"
            f"📅 Напоминание: каждый день в {morning_time}",
            parse_mode="Markdown"
        )
        await state.clear()

    await call.answer()


async def _create_habit_with_schedule(telegram_id: int, preset: dict, reminder_times: list, reminder_days: str, target_value: int = None):
    """Создать привычку с расписанием напоминаний"""
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(telegram_id)
        habit_service = HabitService(session)

        habit = await habit_service.create_habit(
            user_id=user.id,
            name=preset["name"],
            emoji=preset["emoji"],
            target_value=target_value or preset.get("target_value"),
            unit=preset.get("unit")
        )

        if habit:
            # Сохраняем расписание напоминаний
            habit.reminder_times = json.dumps(reminder_times)
            habit.reminder_days = reminder_days
            habit.reminder_enabled = True
            await session.commit()

        return habit


async def _create_custom_habit_with_schedule(telegram_id: int, name: str, emoji: str, reminder_times: list, reminder_days: str, target_value: int = None, unit: str = None):
    """Создать кастомную привычку с расписанием напоминаний"""
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(telegram_id)
        habit_service = HabitService(session)

        habit = await habit_service.create_habit(
            user_id=user.id,
            name=name,
            emoji=emoji,
            target_value=target_value,
            unit=unit
        )

        if habit:
            # Сохраняем расписание напоминаний
            habit.reminder_times = json.dumps(reminder_times)
            habit.reminder_days = reminder_days
            habit.reminder_enabled = True
            await session.commit()

        return habit


# --- FSM обработчики для настройки привычек ---

@router.callback_query(F.data.startswith("hday_"))
async def habit_day_callback(call: types.CallbackQuery, state: FSMContext):
    """Обработка выбора дней недели"""
    action = call.data.replace("hday_", "")
    data = await state.get_data()
    selected_days = data.get("selected_days", [])
    preset = data.get("habit_preset", {})

    if action == "cancel":
        await state.clear()
        await call.message.edit_text("❌ Добавление привычки отменено")
        await call.answer()
        return

    if action == "done":
        if not selected_days:
            await call.answer("Выбери хотя бы один день!")
            return
        # Переходим к выбору времени
        await state.update_data(selected_days=selected_days)
        await call.message.edit_text(
            f"{preset['emoji']} **{preset['name']}**\n\n"
            f"Дни: {_format_days(selected_days)}\n\n"
            f"Во сколько напоминать?",
            parse_mode="Markdown",
            reply_markup=actions.habit_time_keyboard()
        )
        await state.set_state(HabitSetupStates.waiting_for_time)
        await call.answer()
        return

    if action == "all":
        selected_days = [0, 1, 2, 3, 4, 5, 6]
    elif action == "weekdays":
        selected_days = [0, 1, 2, 3, 4]
    else:
        # Тоггл конкретного дня
        day = int(action)
        if day in selected_days:
            selected_days.remove(day)
        else:
            selected_days.append(day)

    await state.update_data(selected_days=selected_days)
    await call.message.edit_reply_markup(reply_markup=actions.habit_days_keyboard(selected_days))
    await call.answer()


@router.callback_query(F.data.startswith("htime_"))
async def habit_time_callback(call: types.CallbackQuery, state: FSMContext):
    """Обработка выбора времени напоминания"""
    time_str = call.data.replace("htime_", "")
    data = await state.get_data()
    preset = data.get("habit_preset", {})
    selected_days = data.get("selected_days", [0, 1, 2, 3, 4, 5, 6])

    # Проверяем, это кастомная привычка или из пресетов
    custom_name = data.get("custom_habit_name")
    if custom_name:
        # Кастомная привычка
        emoji = data.get("custom_habit_emoji", "✅")
        target = data.get("custom_habit_target")
        unit = data.get("custom_habit_unit")

        if time_str == "custom":
            await call.message.edit_text(
                f"{emoji} **{custom_name}**\n\n"
                f"Напиши время напоминания:\n"
                f"• 18:30 — конкретное время\n"
                f"• Каждые 2 часа\n"
                f"• Каждые полтора часа",
                parse_mode="Markdown"
            )
            await state.set_state(HabitSetupStates.waiting_for_custom_time)
            await call.answer()
            return

        # Создаём кастомную привычку
        await _create_custom_habit_with_schedule(
            call.from_user.id,
            name=custom_name,
            emoji=emoji,
            target_value=target,
            unit=unit,
            reminder_times=[time_str],
            reminder_days="0,1,2,3,4,5,6"
        )

        response = f"✅ Привычка добавлена!\n\n{emoji} **{custom_name}**"
        if target and unit:
            response += f" ({target} {unit})"
        response += f"\n📅 Каждый день в {time_str}"

        await call.message.edit_text(response, parse_mode="Markdown")
        await state.clear()
        await call.answer()
        return

    # Пресетная привычка
    if time_str == "custom":
        await call.message.edit_text(
            f"{preset['emoji']} **{preset['name']}**\n\n"
            f"Напиши время напоминания (например, 18:30 или 7 вечера):",
            parse_mode="Markdown"
        )
        await call.answer()
        return

    # Создаём привычку с выбранным временем
    reminder_days = ",".join(str(d) for d in sorted(selected_days))
    await _create_habit_with_schedule(
        call.from_user.id,
        preset,
        reminder_times=[time_str],
        reminder_days=reminder_days
    )

    await call.message.edit_text(
        f"✅ Привычка добавлена!\n\n"
        f"{preset['emoji']} **{preset['name']}**\n"
        f"📅 {_format_days(selected_days)} в {time_str}",
        parse_mode="Markdown"
    )
    await state.clear()
    await call.answer()


@router.callback_query(F.data.startswith("hcount_"))
async def habit_count_callback(call: types.CallbackQuery, state: FSMContext):
    """Обработка выбора количества (вода, витамины)"""
    count = int(call.data.replace("hcount_", ""))
    data = await state.get_data()
    preset = data.get("habit_preset", {})
    habit_key = data.get("habit_key", "")

    # Генерируем расписание напоминаний в зависимости от типа привычки
    if habit_key == "water":
        # Вода: равномерно с 08:00 до 21:00
        reminder_times = _generate_water_schedule(count)
        reminder_days = "0,1,2,3,4,5,6"
        schedule_text = f"{count} стаканов — напоминания каждые ~2 часа"
    elif habit_key == "vitamins":
        # Витамины: утро/обед/вечер
        if count == 1:
            reminder_times = ["08:00"]
            schedule_text = "Утром в 08:00"
        elif count == 2:
            reminder_times = ["08:00", "20:00"]
            schedule_text = "Утром (08:00) и вечером (20:00)"
        else:  # 3
            reminder_times = ["08:00", "13:00", "19:00"]
            schedule_text = "Утром (08:00), в обед (13:00) и вечером (19:00)"
        reminder_days = "0,1,2,3,4,5,6"
    else:
        reminder_times = ["08:00"]
        reminder_days = "0,1,2,3,4,5,6"
        schedule_text = "Каждый день в 08:00"

    await _create_habit_with_schedule(
        call.from_user.id,
        preset,
        reminder_times=reminder_times,
        reminder_days=reminder_days,
        target_value=count
    )

    await call.message.edit_text(
        f"✅ Привычка добавлена!\n\n"
        f"{preset['emoji']} **{preset['name']}**\n"
        f"📅 {schedule_text}",
        parse_mode="Markdown"
    )
    await state.clear()
    await call.answer()


@router.callback_query(F.data.startswith("hinterval_"))
async def habit_interval_callback(call: types.CallbackQuery, state: FSMContext):
    """Обработка выбора интервала напоминаний (для воды)"""
    interval = int(call.data.replace("hinterval_", ""))  # 30, 60, 120, 180
    data = await state.get_data()
    preset = data.get("habit_preset", {})

    # Форматируем текст интервала
    if interval == 30:
        interval_text = "каждые 30 минут"
    elif interval == 60:
        interval_text = "каждый час"
    elif interval == 120:
        interval_text = "каждые 2 часа"
    else:  # 180
        interval_text = "каждые 3 часа"

    # Создаём привычку с интервалом
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        # Используем режим пользователя
        morning_time = user.morning_time or "08:00"
        evening_time = user.evening_time or "22:00"

        habit = await habit_service.create_habit(
            user_id=user.id,
            name=preset["name"],
            emoji=preset["emoji"],
            target_value=None,  # Без подсчёта стаканов
            unit=None,
            frequency="daily"
        )

        if habit:
            # Устанавливаем интервал напоминаний
            habit.reminder_interval_minutes = interval
            habit.reminder_days = "0,1,2,3,4,5,6"
            habit.reminder_enabled = True
            await session.commit()

    await call.message.edit_text(
        f"✅ Привычка добавлена!\n\n"
        f"{preset['emoji']} **{preset['name']}**\n"
        f"📅 Напоминания {interval_text} ({morning_time}–{evening_time})",
        parse_mode="Markdown"
    )
    await state.clear()
    await call.answer()


@router.callback_query(F.data.startswith("htod_"))
async def habit_time_of_day_callback(call: types.CallbackQuery, state: FSMContext):
    """Обработка выбора времени суток (утро/вечер)"""
    tod = call.data.replace("htod_", "")
    data = await state.get_data()
    preset = data.get("habit_preset", {})

    # Берём время из режима пользователя
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)
        morning_time = user.morning_time or "08:00"
        evening_time = user.evening_time or "22:00"
        # Вечернее напоминание за час до конца режима
        hour, minute = map(int, evening_time.split(":"))
        evening_reminder = f"{hour - 1 if hour > 0 else 23:02d}:{minute:02d}"

    if tod == "morning":
        reminder_times = [morning_time]
        schedule_text = f"Каждый день утром ({morning_time})"
    elif tod == "evening":
        reminder_times = [evening_reminder]
        schedule_text = f"Каждый день вечером ({evening_reminder})"
    else:  # both
        reminder_times = [morning_time, evening_reminder]
        schedule_text = f"Утром ({morning_time}) и вечером ({evening_reminder})"

    await _create_habit_with_schedule(
        call.from_user.id,
        preset,
        reminder_times=reminder_times,
        reminder_days="0,1,2,3,4,5,6"
    )

    await call.message.edit_text(
        f"✅ Привычка добавлена!\n\n"
        f"{preset['emoji']} **{preset['name']}**\n"
        f"📅 {schedule_text}",
        parse_mode="Markdown"
    )
    await state.clear()
    await call.answer()


@router.message(HabitSetupStates.waiting_for_time)
async def habit_custom_time_handler(message: types.Message, state: FSMContext):
    """Обработка ввода времени текстом"""
    text = message.text.strip()
    data = await state.get_data()
    preset = data.get("habit_preset", {})
    selected_days = data.get("selected_days", [0, 1, 2, 3, 4, 5, 6])

    # Парсим время
    time_str = _parse_time(text)
    if not time_str:
        await message.answer("Не понял время. Напиши например: 18:00 или 7 вечера")
        return

    reminder_days = ",".join(str(d) for d in sorted(selected_days))
    await _create_habit_with_schedule(
        message.from_user.id,
        preset,
        reminder_times=[time_str],
        reminder_days=reminder_days
    )

    await message.answer(
        f"✅ Привычка добавлена!\n\n"
        f"{preset['emoji']} **{preset['name']}**\n"
        f"📅 {_format_days(selected_days)} в {time_str}",
        parse_mode="Markdown"
    )
    await state.clear()


@router.message(HabitSetupStates.waiting_for_bedtime)
async def habit_bedtime_handler(message: types.Message, state: FSMContext):
    """Обработка ввода времени сна"""
    text = message.text.strip()
    data = await state.get_data()
    preset = data.get("habit_preset", {})

    # Парсим время
    time_str = _parse_time(text)
    if not time_str:
        await message.answer("Не понял время. Напиши например: 23:00 или 11 вечера")
        return

    # Напоминание за час до сна
    hour, minute = map(int, time_str.split(":"))
    reminder_hour = (hour - 1) % 24
    reminder_time = f"{reminder_hour:02d}:{minute:02d}"

    await _create_habit_with_schedule(
        message.from_user.id,
        preset,
        reminder_times=[reminder_time],
        reminder_days="0,1,2,3,4,5,6"
    )

    await message.answer(
        f"✅ Привычка добавлена!\n\n"
        f"{preset['emoji']} **{preset['name']}**\n"
        f"🛏 Сон в {time_str}\n"
        f"📅 Напоминание каждый день в {reminder_time} (за час до сна)",
        parse_mode="Markdown"
    )
    await state.clear()


def _format_days(days: list) -> str:
    """Форматирование списка дней"""
    if set(days) == {0, 1, 2, 3, 4, 5, 6}:
        return "Каждый день"
    if set(days) == {0, 1, 2, 3, 4}:
        return "По будням"
    if set(days) == {5, 6}:
        return "По выходным"

    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return ", ".join(day_names[d] for d in sorted(days))


def _format_reminder_text(reminder_minutes: list[int] | None) -> str:
    """Форматирование текста напоминаний"""
    if reminder_minutes is None:
        reminder_minutes = [60, 15]

    def days_word(n):
        if n == 1:
            return "день"
        elif 2 <= n <= 4:
            return "дня"
        else:
            return "дней"

    def hours_word(n):
        if n == 1:
            return "час"
        elif 2 <= n <= 4:
            return "часа"
        else:
            return "часов"

    parts = []
    for mins in sorted(reminder_minutes, reverse=True):
        if mins >= 1440:
            days = mins // 1440
            parts.append(f"За {days} {days_word(days)}")
        elif mins >= 60:
            hours = mins // 60
            parts.append(f"За {hours} {hours_word(hours)}")
        else:
            parts.append(f"За {mins} мин")

    return "; ".join(parts) + " до"


def _parse_time(text: str) -> str | None:
    """Парсинг времени из текста"""
    import re

    # Формат HH:MM или H:MM
    match = re.match(r'^(\d{1,2})[:\.](\d{2})$', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"

    # Формат "в 18" или "18"
    match = re.match(r'^(?:в\s*)?(\d{1,2})$', text)
    if match:
        hour = int(match.group(1))
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"

    # Формат "7 вечера", "8 утра"
    match = re.match(r'^(\d{1,2})\s*(утра|вечера|дня|ночи)$', text.lower())
    if match:
        hour = int(match.group(1))
        period = match.group(2)
        if period in ["вечера", "ночи"] and hour < 12:
            hour += 12
        elif period == "дня" and hour < 12:
            hour += 12
        elif period == "утра" and hour == 12:
            hour = 0
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"

    return None


def _parse_interval(text: str) -> list | None:
    """Парсинг интервала и генерация расписания напоминаний.
    Возвращает список времён или None если не распознан интервал.

    Примеры:
    - "каждые 2 часа" -> ["08:00", "10:00", "12:00", ...]
    - "каждые 1,5 часа" -> ["08:00", "09:30", "11:00", ...]
    - "каждый час" -> ["08:00", "09:00", "10:00", ...]
    - "каждые 30 минут" -> ["08:00", "08:30", "09:00", ...]
    """
    import re

    text_lower = text.lower().strip()

    # "каждый час" / "каждые час"
    if re.match(r'^кажд[ыйую][йея]?\s+час$', text_lower):
        return _generate_interval_schedule(60)

    # "каждые полтора часа" / "каждые полчаса"
    if re.match(r'^кажд[ыйую][йея]?\s+полтора\s*час[аов]*$', text_lower):
        return _generate_interval_schedule(90)  # 1.5 часа = 90 минут

    if re.match(r'^кажд[ыйую][йея]?\s+полчаса$', text_lower):
        return _generate_interval_schedule(30)

    # "каждые N часов/часа" (целое число)
    match = re.match(r'^кажд[ыйую][йея]?\s+(\d+)\s*час[аов]*$', text_lower)
    if match:
        hours = int(match.group(1))
        return _generate_interval_schedule(hours * 60)

    # "каждые N,M часа" или "каждые N.M часа" (дробное)
    match = re.match(r'^кажд[ыйую][йея]?\s+(\d+)[,\.](\d+)\s*час[аов]*$', text_lower)
    if match:
        hours = int(match.group(1))
        decimal = int(match.group(2))
        # 1,5 -> 1.5, 2,5 -> 2.5
        total_hours = hours + decimal / 10
        return _generate_interval_schedule(int(total_hours * 60))

    # "каждые N минут"
    match = re.match(r'^кажд[ыйую][йея]?\s+(\d+)\s*мин[у|а-я]*$', text_lower)
    if match:
        minutes = int(match.group(1))
        if minutes >= 15:  # Минимум 15 минут
            return _generate_interval_schedule(minutes)

    # "раз в полтора часа" / "раз в полчаса"
    if re.match(r'^раз\s+в\s+полтора\s*час[аов]*$', text_lower):
        return _generate_interval_schedule(90)

    if re.match(r'^раз\s+в\s+полчаса$', text_lower):
        return _generate_interval_schedule(30)

    # "раз в N часов/часа"
    match = re.match(r'^раз\s+в\s+(\d+)\s*час[аов]*$', text_lower)
    if match:
        hours = int(match.group(1))
        return _generate_interval_schedule(hours * 60)

    # "раз в N,M часа"
    match = re.match(r'^раз\s+в\s+(\d+)[,\.](\d+)\s*час[аов]*$', text_lower)
    if match:
        hours = int(match.group(1))
        decimal = int(match.group(2))
        total_hours = hours + decimal / 10
        return _generate_interval_schedule(int(total_hours * 60))

    return None


def _generate_interval_schedule(interval_minutes: int, start_hour: int = 8, end_hour: int = 22) -> list:
    """Генерация расписания напоминаний с заданным интервалом.

    Args:
        interval_minutes: Интервал в минутах между напоминаниями
        start_hour: Час начала (по умолчанию 8:00)
        end_hour: Час окончания (по умолчанию 22:00)

    Returns:
        Список времён в формате ["HH:MM", ...]
    """
    times = []
    current_minutes = start_hour * 60  # Начинаем с start_hour:00
    end_minutes = end_hour * 60

    while current_minutes < end_minutes:
        hour = current_minutes // 60
        minute = current_minutes % 60
        times.append(f"{hour:02d}:{minute:02d}")
        current_minutes += interval_minutes

    return times if times else None


def _generate_water_schedule(glasses: int) -> list:
    """Генерация расписания напоминаний о воде"""
    # С 08:00 до 21:00 — 13 часов
    # Например 8 стаканов = каждые ~1.6 часа
    start_hour = 8
    end_hour = 21
    interval = (end_hour - start_hour) / glasses

    times = []
    for i in range(glasses):
        hour = int(start_hour + i * interval)
        times.append(f"{hour:02d}:00")

    return times


@router.callback_query(F.data == "habit_show_add")
async def habit_show_add_callback(call: types.CallbackQuery):
    """Показать кнопки добавления привычек (без уже добавленных)"""
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)
        habits = await habit_service.get_user_habits(user.id)
        existing_names = [h.name for h in habits]

    await call.message.edit_text(
        "📋 **Добавить привычку**\n\nВыбери или напиши свою: `/habit_add Йога`",
        parse_mode="Markdown",
        reply_markup=actions.habits_add_keyboard(existing_names)
    )
    await call.answer()


@router.callback_query(F.data.startswith("habit_done_"))
async def habit_done_callback(call: types.CallbackQuery):
    """Отметка привычки выполненной по кнопке"""
    habit_id = int(call.data.replace("habit_done_", ""))

    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        # Получаем привычку
        from sqlalchemy import select
        from database.models import Habit, HabitLog
        result = await session.execute(select(Habit).where(Habit.id == habit_id))
        habit = result.scalar_one_or_none()

        if not habit:
            await call.answer("Привычка не найдена")
            return

        # Определяем effective_target:
        # 1. Явный target_value
        # 2. Количество интервалов (reminder_interval_minutes)
        # 3. Количество reminder_times
        effective_target = habit.target_value

        # Для интервальных привычек (вода каждый час)
        if effective_target is None and habit.reminder_interval_minutes:
            try:
                morning_time = user.morning_time or "08:00"
                evening_time = user.evening_time or "22:00"
                start_h = int(morning_time.split(":")[0])
                end_h = int(evening_time.split(":")[0])
                total_minutes = (end_h - start_h) * 60
                effective_target = total_minutes // habit.reminder_interval_minutes
            except (ValueError, AttributeError):
                pass

        # Для привычек с несколькими фиксированными напоминаниями
        if effective_target is None and habit.reminder_times:
            try:
                import json
                times = json.loads(habit.reminder_times)
                if isinstance(times, list) and len(times) > 1:
                    effective_target = len(times)
            except (json.JSONDecodeError, TypeError):
                pass

        # Для привычек со счётчиком — увеличиваем значение
        new_value = 1
        if effective_target:
            # Проверяем текущий прогресс за сегодня
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            existing = await session.execute(
                select(HabitLog).where(
                    HabitLog.habit_id == habit_id,
                    HabitLog.user_id == user.id,
                    HabitLog.date >= today
                )
            )
            log = existing.scalar_one_or_none()
            if log:
                new_value = log.value + 1
            else:
                new_value = 1

        # Логируем с новым значением
        log, xp_earned, new_achievements = await habit_service.log_habit(
            habit_id=habit.id,
            user_id=user.id,
            value=new_value
        )

        # Обучаем смарт-привычки (записываем реакцию)
        try:
            from services.smart_habits_service import SmartHabitsService
            smart_service = SmartHabitsService(session)
            await smart_service.record_reminder_response(habit, was_acted_on=True)

            # Раз в неделю обновляем выученные времена
            if habit.last_reminder_adjust is None or \
               (datetime.now() - habit.last_reminder_adjust).days >= 7:
                await smart_service.update_learned_times(habit)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Ошибка обучения смарт-привычки: {e}")

        # Показываем уведомление
        if effective_target:
            notify = f"{habit.emoji} {habit.name}: {new_value}/{effective_target}"
            if new_value >= effective_target:
                notify += " ✅"
        else:
            notify = f"✅ {habit.name}"

        if xp_earned > 0:
            notify += f" +{xp_earned} XP"
        await call.answer(notify)

        # Обновляем сообщение с полным статусом и кнопками
        habits = await habit_service.get_user_habits(user.id)
        status = await habit_service.get_today_status(user.id)
        response = habit_service.format_habits_message(status)

        # Ачивки временно отключены
        # for ach_key in new_achievements:
        #     response += f"\n\n{habit_service.format_achievement_message(ach_key)}"

        keyboard = actions.habits_checkin_keyboard(habits)
        await call.message.edit_text(response, parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data == "habit_show_delete")
async def habit_show_delete_callback(call: types.CallbackQuery):
    """Показать кнопки удаления привычек"""
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        habits = await habit_service.get_user_habits(user.id)

        if not habits:
            await call.answer("У тебя нет привычек для удаления")
            return

        await call.message.edit_text(
            "🗑 **Удалить привычку**\n\nВыбери какую удалить:",
            parse_mode="Markdown",
            reply_markup=actions.habits_delete_keyboard(habits)
        )
        await call.answer()


@router.callback_query(F.data.startswith("habit_delete_"))
async def habit_delete_callback(call: types.CallbackQuery):
    """Удаление привычки по кнопке"""
    habit_id = int(call.data.replace("habit_delete_", ""))

    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        # Получаем привычку для названия
        from sqlalchemy import select
        from database.models import Habit
        result = await session.execute(select(Habit).where(Habit.id == habit_id))
        habit = result.scalar_one_or_none()

        if not habit:
            await call.answer("Привычка не найдена")
            return

        # Удаляем
        success = await habit_service.delete_habit(habit_id, user.id)

        if success:
            await call.answer(f"🗑 {habit.name} удалена")

            # Обновляем список
            habits = await habit_service.get_user_habits(user.id)
            status = await habit_service.get_today_status(user.id)
            response = habit_service.format_habits_message(status)
            keyboard = actions.habits_checkin_keyboard(habits) if habits else None

            await call.message.edit_text(response, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await call.answer("❌ Не удалось удалить")


@router.callback_query(F.data == "habit_show_edit_time")
async def habit_show_edit_time_callback(call: types.CallbackQuery):
    """Показать кнопки редактирования времени привычек"""
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        habits = await habit_service.get_user_habits(user.id)

        if not habits:
            await call.answer("У тебя нет привычек")
            return

        await call.message.edit_text(
            "⏰ **Настройка времени напоминаний**\n\nВыбери привычку:",
            parse_mode="Markdown",
            reply_markup=actions.habits_edit_time_keyboard(habits)
        )
        await call.answer()


@router.callback_query(F.data.startswith("habit_edit_time_"))
async def habit_edit_time_callback(call: types.CallbackQuery, state: FSMContext):
    """Показать текущее время и предложить изменить"""
    habit_id = int(call.data.replace("habit_edit_time_", ""))

    async with async_session() as session:
        from sqlalchemy import select
        from database.models import Habit
        result = await session.execute(select(Habit).where(Habit.id == habit_id))
        habit = result.scalar_one_or_none()

        if not habit:
            await call.answer("Привычка не найдена")
            return

        # Показываем текущие времена
        current_times = json.loads(habit.reminder_times) if habit.reminder_times else []
        times_str = ", ".join(current_times) if current_times else "не установлено"

        # Кнопки с предустановленными вариантами
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="1️⃣ Утро (08:00)", callback_data=f"htime_{habit_id}_1_08:00")],
            [InlineKeyboardButton(text="2️⃣ Утро + Вечер", callback_data=f"htime_{habit_id}_2_08:00,20:00")],
            [InlineKeyboardButton(text="3️⃣ Утро + День + Вечер", callback_data=f"htime_{habit_id}_3_08:00,13:00,19:00")],
            [InlineKeyboardButton(text="✏️ Своё время", callback_data=f"htime_custom_{habit_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="habit_show_edit_time")]
        ])

        await call.message.edit_text(
            f"⏰ **{habit.emoji} {habit.name}**\n\n"
            f"Текущее время: {times_str}\n\n"
            f"Выбери когда напоминать:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        await call.answer()


@router.callback_query(F.data.startswith("htime_custom_"))
async def habit_time_custom_callback(call: types.CallbackQuery, state: FSMContext):
    """Ввод своего времени"""
    habit_id = int(call.data.replace("htime_custom_", ""))

    await state.set_state(HabitSetupStates.waiting_for_custom_time)
    await state.update_data(edit_habit_id=habit_id)

    await call.message.edit_text(
        "✏️ Напиши время напоминаний через запятую.\n\n"
        "Примеры:\n"
        "• `08:00` — один раз утром\n"
        "• `08:00, 14:00, 20:00` — три раза в день\n"
        "• `09:00, 11:00, 13:00, 15:00, 17:00` — каждые 2 часа",
        parse_mode="Markdown"
    )
    await call.answer()


@router.message(HabitSetupStates.waiting_for_custom_time)
async def habit_custom_time_input(message: types.Message, state: FSMContext):
    """Обработка ввода кастомного времени (для редактирования и создания)"""
    import re

    text = message.text.strip()
    data = await state.get_data()

    # Проверяем: это редактирование существующей привычки или создание новой?
    habit_id = data.get("edit_habit_id")
    custom_name = data.get("custom_habit_name")

    # === СОЗДАНИЕ НОВОЙ ПРИВЫЧКИ ===
    if custom_name and not habit_id:
        emoji = data.get("custom_habit_emoji", "✅")
        target = data.get("custom_habit_target")
        unit = data.get("custom_habit_unit")

        # Сначала пробуем распознать интервал ("каждые 2 часа", "каждые 1,5 часа")
        interval_times = _parse_interval(text)
        if interval_times:
            await _create_custom_habit_with_schedule(
                message.from_user.id,
                name=custom_name,
                emoji=emoji,
                target_value=target,
                unit=unit,
                reminder_times=interval_times,
                reminder_days="0,1,2,3,4,5,6"
            )

            response = f"✅ Привычка добавлена!\n\n{emoji} **{custom_name}**"
            if target and unit:
                response += f" ({target} {unit})"
            response += f"\n📅 Напоминания: {len(interval_times)} раз в день ({interval_times[0]} - {interval_times[-1]})"

            await message.answer(response, parse_mode="Markdown")
            await state.clear()
            return

        # Парсим конкретное время
        time_str = _parse_time(text)
        if not time_str:
            await message.answer(
                "Не понял время. Напиши например:\n"
                "• 18:00 или 07:30\n"
                "• Каждые 2 часа\n"
                "• Каждые 1,5 часа"
            )
            return

        await _create_custom_habit_with_schedule(
            message.from_user.id,
            name=custom_name,
            emoji=emoji,
            target_value=target,
            unit=unit,
            reminder_times=[time_str],
            reminder_days="0,1,2,3,4,5,6"
        )

        response = f"✅ Привычка добавлена!\n\n{emoji} **{custom_name}**"
        if target and unit:
            response += f" ({target} {unit})"
        response += f"\n📅 Каждый день в {time_str}"

        await message.answer(response, parse_mode="Markdown")
        await state.clear()
        return

    # === РЕДАКТИРОВАНИЕ СУЩЕСТВУЮЩЕЙ ПРИВЫЧКИ ===
    if habit_id:
        # Сначала пробуем интервал
        interval_times = _parse_interval(text)
        if interval_times:
            normalized = interval_times
        else:
            # Парсим времена: 08:00, 14:00 или 08:00 14:00
            times = re.findall(r'\d{1,2}:\d{2}', text)

            if not times:
                await message.answer(
                    "Не понял время. Напиши например:\n"
                    "• 08:00, 14:00\n"
                    "• Каждые 2 часа\n"
                    "• Каждые 1,5 часа"
                )
                return

            # Нормализуем формат
            normalized = []
            for t in times:
                parts = t.split(":")
                h, m = int(parts[0]), int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    normalized.append(f"{h:02d}:{m:02d}")

            if not normalized:
                await message.answer("❌ Некорректное время. Проверь формат.")
                return

        async with async_session() as session:
            from sqlalchemy import select
            from database.models import Habit
            result = await session.execute(select(Habit).where(Habit.id == habit_id))
            habit = result.scalar_one_or_none()

            if habit:
                habit.reminder_times = json.dumps(normalized)
                habit.reminder_enabled = True
                await session.commit()

                times_str = ", ".join(normalized) if len(normalized) <= 5 else f"{len(normalized)} раз/день"
                await message.answer(
                    f"✅ Время обновлено!\n\n"
                    f"{habit.emoji} **{habit.name}**\n"
                    f"⏰ Напоминания: {times_str}",
                    parse_mode="Markdown"
                )
            else:
                await message.answer("❌ Привычка не найдена")

        await state.clear()
        return

    # Если ни то ни другое — ошибка состояния
    await message.answer("❌ Что-то пошло не так. Попробуй снова.")
    await state.clear()


@router.callback_query(F.data.regexp(r"htime_\d+_\d+_.+"))
async def habit_time_preset_callback(call: types.CallbackQuery):
    """Установка предустановленного времени"""
    # htime_10_3_08:00,13:00,19:00
    parts = call.data.split("_")
    habit_id = int(parts[1])
    times_str = parts[3]  # "08:00,13:00,19:00"
    times = times_str.split(",")

    async with async_session() as session:
        from sqlalchemy import select
        from database.models import Habit
        result = await session.execute(select(Habit).where(Habit.id == habit_id))
        habit = result.scalar_one_or_none()

        if not habit:
            await call.answer("Привычка не найдена")
            return

        habit.reminder_times = json.dumps(times)
        habit.reminder_enabled = True
        await session.commit()

        # Обновляем сообщение
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        habits = await habit_service.get_user_habits(user.id)
        status = await habit_service.get_today_status(user.id)
        response = habit_service.format_habits_message(status)
        keyboard = actions.habits_checkin_keyboard(habits)

        times_display = ", ".join(times)
        await call.message.edit_text(
            f"✅ Время обновлено!\n\n"
            f"{habit.emoji} **{habit.name}**\n"
            f"⏰ Напоминания: {times_display}\n\n"
            f"---\n\n{response}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        await call.answer("✅ Сохранено")


@router.callback_query(F.data == "habit_back")
async def habit_back_callback(call: types.CallbackQuery):
    """Вернуться к списку привычек"""
    async with async_session() as session:
        habit_service = HabitService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        habits = await habit_service.get_user_habits(user.id)
        status = await habit_service.get_today_status(user.id)
        response = habit_service.format_habits_message(status)
        keyboard = actions.habits_checkin_keyboard(habits) if habits else None

        await call.message.edit_text(response, parse_mode="Markdown", reply_markup=keyboard)
        await call.answer()


# --- УТРЕННИЙ ЧЕК-ИН CALLBACKS ---

@router.callback_query(F.data.startswith("sleep_"))
async def morning_sleep_callback(call: types.CallbackQuery, state: FSMContext):
    """Оценка качества сна"""
    sleep_quality = call.data.replace("sleep_", "")
    quality_map = {
        "great": "😴 Отлично",
        "good": "😊 Хорошо",
        "ok": "😐 Нормально",
        "bad": "😩 Плохо"
    }

    # Сохраняем в FSM
    data = await state.get_data()
    checkin = data.get("morning_checkin", {})
    checkin["sleep_quality"] = sleep_quality
    await state.update_data(morning_checkin=checkin)
    await state.set_state(MorningCheckinStates.waiting_for_bedtime)

    response = f"Сон: {quality_map.get(sleep_quality, sleep_quality)}\n\n"
    response += "Во сколько лёг спать?"

    await call.message.edit_text(
        response,
        reply_markup=actions.morning_bedtime_keyboard()
    )
    await call.answer()


@router.callback_query(F.data.startswith("bed_"))
async def morning_bedtime_callback(call: types.CallbackQuery, state: FSMContext):
    """Время отхода ко сну"""
    bedtime = call.data.replace("bed_", "")
    time_map = {
        "22": "22:00", "23": "23:00", "00": "00:00",
        "01": "01:00", "02": "02:00", "late": "позже 2:00"
    }

    # Сохраняем в FSM
    data = await state.get_data()
    checkin = data.get("morning_checkin", {})
    checkin["bedtime"] = time_map.get(bedtime, bedtime)
    await state.update_data(morning_checkin=checkin)
    await state.set_state(MorningCheckinStates.waiting_for_wakeup)

    response = f"Лёг: {time_map.get(bedtime, bedtime)}\n\n"
    response += "Во сколько встал?"

    await call.message.edit_text(
        response,
        reply_markup=actions.morning_wakeup_keyboard()
    )
    await call.answer()


@router.callback_query(F.data.startswith("wake_"))
async def morning_wakeup_callback(call: types.CallbackQuery, state: FSMContext):
    """Время подъёма"""
    wakeup = call.data.replace("wake_", "")
    time_map = {
        "6": "6:00", "7": "7:00", "8": "8:00",
        "9": "9:00", "10": "10:00", "late": "позже 10:00"
    }

    # Сохраняем в FSM
    data = await state.get_data()
    checkin = data.get("morning_checkin", {})
    checkin["wakeup_time"] = time_map.get(wakeup, wakeup)
    await state.update_data(morning_checkin=checkin)
    await state.set_state(MorningCheckinStates.waiting_for_water)

    response = f"Встал: {time_map.get(wakeup, wakeup)}\n\n"
    response += "💧 Выпей стакан воды!"

    await call.message.edit_text(
        response,
        reply_markup=actions.morning_water_keyboard()
    )
    await call.answer()


@router.callback_query(F.data.in_(["water_done", "water_skip"]))
async def morning_water_callback(call: types.CallbackQuery, state: FSMContext):
    """Вода выпита — сохраняем данные и показываем расписание на день"""
    import pytz
    from database.models import SleepLog

    water_done = call.data == "water_done"

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        # Получаем собранные данные из FSM
        data = await state.get_data()
        checkin = data.get("morning_checkin", {})
        checkin["water_drunk"] = water_done

        # Сохраняем в базу данных
        sleep_log = SleepLog(
            user_id=user.id,
            sleep_quality=checkin.get("sleep_quality"),
            bedtime=checkin.get("bedtime"),
            wakeup_time=checkin.get("wakeup_time"),
            water_drunk=water_done,
        )
        session.add(sleep_log)
        await session.commit()

        # Получаем календарь пользователя
        cal = await get_user_calendar_service(call.from_user.id)

        # Формируем итоговое сообщение
        response = "✅ Утренний чек-ин завершён!\n\n"

        if water_done:
            response += "💧 Вода выпита. Молодец!\n\n"

        if cal is not None:
            events = cal.get_events(period="today", only_future=False)
            if events:
                response += "📅 **Сегодня:**\n"
                for event in events:
                    start = event.get("start", {})
                    title = event.get("summary", "Событие")
                    emoji = cal.get_emoji_for_title(title)

                    if "dateTime" in start:
                        start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                        start_local = start_dt.astimezone(pytz.timezone(config.TIMEZONE))
                        time_str = start_local.strftime("%H:%M")
                        response += f"• {time_str} — {emoji} {title}\n"
                    else:
                        response += f"• {emoji} {title} (весь день)\n"
            else:
                response += "📭 Сегодня пусто. Свободный день!"
        else:
            response += "📭 Календарь не подключён. Используй /connect_calendar"

        response += "\n\n🎯 Какая главная задача на сегодня?"

        # Переходим в состояние ожидания фокуса
        await state.set_state(MorningCheckinStates.waiting_for_focus)

        # Сохраняем в историю, чтобы AI понимал контекст при следующем ответе
        await memory.save_message(user.id, "assistant", response)

        await call.message.edit_text(response, parse_mode="Markdown")
        await call.answer()


@router.message(MorningCheckinStates.waiting_for_focus)
async def morning_focus_handler(message: types.Message, state: FSMContext):
    """Обработка ответа на 'Какая главная задача на сегодня?'"""
    from database.models import SleepLog
    from sqlalchemy import select, desc

    focus_task = message.text.strip()

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        # Обновляем последнюю запись SleepLog для этого пользователя
        result = await session.execute(
            select(SleepLog)
            .where(SleepLog.user_id == user.id)
            .order_by(desc(SleepLog.created_at))
            .limit(1)
        )
        sleep_log = result.scalar_one_or_none()
        if sleep_log:
            sleep_log.focus_task = focus_task
            await session.commit()

        # Сбрасываем состояние
        await state.clear()

        # Отвечаем через AI с контекстом
        ai = AIService(session)
        response = await ai.chat(
            user_id=user.id,
            message=focus_task,
            user_name=message.from_user.first_name or "друг",
        )

        await message.answer(response, parse_mode="Markdown")


# --- ВЕЧЕРНЯЯ РЕФЛЕКСИЯ ---

from states import ReflectionStates


@router.callback_query(F.data == "reflection_yes")
async def reflection_yes_callback(call: types.CallbackQuery, state: FSMContext):
    """Пользователь согласился на рефлексию"""
    await state.set_state(ReflectionStates.writing)

    await call.message.edit_text(
        "🌙 Отлично! Расскажи, как прошёл твой день.\n\n"
        "Можешь ответить на любые из этих вопросов:\n"
        "• Что было главным сегодня?\n"
        "• Что получилось хорошо?\n"
        "• Что можно улучшить?\n\n"
        "Просто напиши свои мысли, я сохраню."
    )
    await call.answer()


@router.callback_query(F.data == "reflection_no")
async def reflection_no_callback(call: types.CallbackQuery):
    """Пользователь отказался от рефлексии"""
    await call.message.edit_text("👌 Хорошо, отдыхай! Спокойной ночи 🌙")
    await call.answer()


@router.message(ReflectionStates.writing)
async def reflection_text_handler(message: types.Message, state: FSMContext):
    """Сохраняем рефлексию пользователя"""
    from database.models import DiaryEntry

    reflection_text = message.text.strip()

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        # Сохраняем как запись дневника с тегом "рефлексия"
        entry = DiaryEntry(
            user_id=user.id,
            content=encryption.encrypt(reflection_text),
            tags="рефлексия,вечер",
        )
        session.add(entry)
        await session.commit()

        # Сбрасываем состояние
        await state.clear()

        await message.answer(
            "✨ Записал! Рефлексия — отличная привычка.\n"
            "Спокойной ночи! 🌙"
        )


# --- ГОЛОСОВЫЕ СООБЩЕНИЯ ---

@router.message(F.voice)
async def handle_voice(message: types.Message, state: FSMContext):
    """Обработка голосовых сообщений с поддержкой команд календаря"""
    # Скачиваем файл
    file = await create_bot.bot.get_file(message.voice.file_id)
    file_path = f"voice_{message.voice.file_id}.ogg"
    await create_bot.bot.download_file(file.file_path, file_path)

    async with async_session() as session:
        ai = AIService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        try:
            # Транскрибация
            transcription = await ai.transcribe_audio(file_path, user_id=user.id)

            # Получаем события календаря для контекста
            cal = await get_user_calendar_service(message.from_user.id)
            calendar_events = []
            if cal is not None:
                try:
                    calendar_events = cal.get_events(period="today") + cal.get_events(period="tomorrow")
                except:
                    pass

            # Определяем намерения (как в текстовых сообщениях)
            intent_data = await ai.detect_intent(transcription, user_id=user.id, calendar_events=calendar_events)
            actions = intent_data.get("actions", [])

            # Если старый формат — конвертируем
            if not actions and "intent" in intent_data:
                actions = [intent_data]

            # Если только chat — анализируем как раньше
            if len(actions) == 1 and actions[0].get("intent") == "chat":
                result = await ai.analyze_voice(
                    user_id=user.id,
                    transcription=transcription,
                    user_name=message.from_user.first_name or "друг"
                )
                await message.answer(result, parse_mode="Markdown")
            else:
                # Обрабатываем команды календаря
                responses = await process_calendar_actions(actions, message, state, message.from_user.id)
                if responses:
                    await message.answer("\n\n".join(responses), parse_mode="Markdown")

        except Exception as e:
            await message.answer(f"❌ Ошибка обработки: {e}")
        finally:
            # Удаляем временный файл
            if os.path.exists(file_path):
                os.remove(file_path)


# --- ИЗОБРАЖЕНИЯ ---

@router.message(F.photo)
async def handle_photo(message: types.Message):
    """Обработка изображений (скриншотов)"""
    await message.answer("📸 Анализирую изображение...")

    # Берём фото максимального качества
    photo = message.photo[-1]
    file = await create_bot.bot.get_file(photo.file_id)

    # Скачиваем
    file_path = f"photo_{photo.file_id}.jpg"
    await create_bot.bot.download_file(file.file_path, file_path)

    async with async_session() as session:
        ai = AIService(session)
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        try:
            # Конвертируем в base64
            with open(file_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode()

            # Анализ через GPT-4 Vision
            result = await ai.analyze_image(
                user_id=user.id,
                image_base64=image_base64,
                user_prompt=message.caption,  # Если есть подпись к фото
            )

            await message.answer(f"📸 **Анализ изображения:**\n\n{result}", parse_mode="Markdown")
        except Exception as e:
            await message.answer(f"❌ Ошибка анализа: {e}")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)


# --- УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ТЕКСТА ---

@router.message()
async def handle_all_messages(message: types.Message, state: FSMContext):
    """Умный обработчик всех текстовых сообщений"""
    if not message.text:
        return

    text = message.text.strip()

    # Пропускаем если это кнопка меню (уже обработано выше)
    if text.startswith(("📋", "✅", "💭", "🧠", "🚀")):
        return

    async with async_session() as session:
        ai = AIService(session)
        memory = MemoryService(session)
        limits = LimitsService(session)
        user, is_new = await memory.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

        # Если новый пользователь — показываем приветствие и просим /start
        if is_new:
            name = message.from_user.first_name or ""
            greeting = f"Привет{', ' + name if name else ''}!" if name else "Привет!"
            await message.answer(
                f"{greeting} Я Джарвис — твой личный AI-ассистент.\n\n"
                "Нажми /start чтобы начать и настроить бота под себя.",
                reply_markup=actions.main_menu()
            )
            return

        # Проверяем лимит AI запросов
        can_use, limit_error = await limits.can_use_ai(user.id)
        if not can_use:
            plan_name = get_plan_name(user.subscription_plan or "free")
            await message.answer(
                f"⚠️ {limit_error}\n\n"
                f"Перейдите на более высокий тариф для увеличения лимита.",
                parse_mode="Markdown"
            )
            return

        # Получаем события календаря для контекста
        cal = await get_user_calendar_service(message.from_user.id)
        calendar_events = []
        if cal is not None:
            try:
                calendar_events = cal.get_events(period="today") + cal.get_events(period="tomorrow")
            except:
                pass

        # Определяем намерения пользователя (может быть несколько!) с контекстом
        intent_data = await ai.detect_intent(text, user_id=user.id, calendar_events=calendar_events)
        actions = intent_data.get("actions", [])

        # Если старый формат (без actions) — конвертируем
        if not actions and "intent" in intent_data:
            actions = [intent_data]

        # Если только chat — отвечаем через AI
        if len(actions) == 1 and actions[0].get("intent") == "chat":
            response = await ai.chat(
                user_id=user.id,
                message=text,
                user_name=message.from_user.first_name or "друг",
            )

            # Увеличиваем счётчик использования AI
            await limits.increment_ai_usage(user.id)

            await message.answer(response, parse_mode="Markdown")

            # Извлекаем контекст (GPT сам решит, нужно ли что-то запомнить)
            await ai.extract_context(user.id, text, response)
            return

        # Обрабатываем команды календаря
        responses = await process_calendar_actions(actions, message, state, message.from_user.id)
        if responses:
            full_response = "\n\n".join(responses)
            await message.answer(full_response, parse_mode="Markdown")

            # Сохраняем в память
            await ai.memory.save_message(user.id, "user", f"[Команда] {text}")
            await ai.memory.save_message(user.id, "assistant", full_response)


async def handle_create_task(action: dict, message: types.Message = None, state: FSMContext = None, telegram_id: int = None) -> str:
    """Обработка создания задачи с проверкой конфликтов"""
    title = action.get("title", "Задача")
    date_str = action.get("date")
    time_str = action.get("time")
    duration = action.get("duration_minutes", 60)
    recurrence = action.get("recurrence")  # daily, weekly, monthly или None
    reminder_minutes = action.get("reminder_minutes")  # Кастомные напоминания [1440, 60]
    location = action.get("location")  # Место события

    try:
        # Проверяем лимит задач в календаре
        if telegram_id:
            async with async_session() as session:
                limits = LimitsService(session)
                memory = MemoryService(session)
                user, _ = await memory.get_or_create_user(telegram_id)
                can_create, limit_error = await limits.can_create_calendar_task(user.id)
                if not can_create:
                    return f"⚠️ {limit_error}\n\nПерейдите на более высокий тариф."

        cal = await get_user_calendar_service(telegram_id) if telegram_id else None
        if cal is None:
            return "❌ Календарь не подключён. Используй /connect_calendar"

        # Если есть время — проверяем конфликты
        if time_str:
            event_datetime = cal.parse_datetime_from_text(date_str, time_str)
            end_datetime = event_datetime + timedelta(minutes=duration)

            # Для повторяющихся событий не проверяем конфликты
            if not recurrence:
                # Проверяем конфликты
                conflicts = cal.check_conflicts(event_datetime, end_datetime)

                # Если есть конфликты и есть state — ждём подтверждения
                if conflicts and state is not None:
                    # Сохраняем данные события в state для создания после подтверждения
                    await state.update_data(
                        pending_event={
                            "title": title,
                            "start_datetime": event_datetime.isoformat(),
                            "duration": duration,
                            "reminder_minutes": reminder_minutes,
                        }
                    )
                    await state.set_state(ConfirmConflictStates.waiting_for_confirmation)

                    # Формируем предупреждение о конфликте
                    conflict_warning = cal.format_conflict_warning(conflicts)
                    emoji = cal.get_emoji_for_title(title)

                    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
                    weekday = weekdays[event_datetime.weekday()]
                    time_start = event_datetime.strftime("%H:%M")
                    time_end = end_datetime.strftime("%H:%M")

                    now = datetime.now(cal.timezone)
                    if event_datetime.date() == now.date():
                        date_label = f"Сегодня ({weekday})"
                    elif event_datetime.date() == (now + timedelta(days=1)).date():
                        date_label = f"Завтра ({weekday})"
                    else:
                        date_label = f"({weekday}) {event_datetime.day} {months[event_datetime.month - 1]}"

                    response = f"⚠️ **Обнаружено пересечение!**\n\n"
                    response += f"{emoji} [{title}]\n"
                    response += f" · Дата: {date_label}\n"
                    response += f" · Время: {time_start} - {time_end}\n\n"
                    response += conflict_warning
                    response += f"\n\n**Добавить всё равно?** (да/нет)"

                    return response

            # Создаём событие (обычное или повторяющееся)
            if recurrence:
                created_event = cal.create_recurring_event(
                    title=title,
                    start_datetime=event_datetime,
                    duration_minutes=duration,
                    recurrence=recurrence,
                    reminder_minutes=reminder_minutes,
                    location=location,
                )
            else:
                created_event = cal.create_event(
                    title=title,
                    start_datetime=event_datetime,
                    duration_minutes=duration,
                    reminder_minutes=reminder_minutes,
                    location=location,
                )

            # Планируем точные напоминания
            event_id = created_event.get("id") if created_event else None

            # Сохраняем задачу в БД и планируем точные напоминания
            async with async_session() as session:
                from database.models import Task
                from services.exact_reminder_service import ExactReminderService
                memory = MemoryService(session)
                user, _ = await memory.get_or_create_user(telegram_id)
                task = Task(
                    user_id=user.id,
                    title=title,
                    due_date=event_datetime,
                    status="pending"
                )
                session.add(task)

                # Увеличиваем счётчик задач в календаре
                limits = LimitsService(session)
                await limits.increment_calendar_task_usage(user.id)

                # Планируем точные напоминания (если есть event_id)
                if event_id and not recurrence:  # Для повторяющихся событий пока не поддерживаем
                    exact_service = ExactReminderService(session)
                    await exact_service.schedule_reminders_for_event(
                        user_id=user.id,
                        telegram_id=telegram_id,
                        event_id=event_id,
                        event_title=title,
                        event_time=event_datetime,
                    )

                await session.commit()

            # Форматируем ответ
            weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            weekdays_full = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
            months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря"]

            weekday = weekdays[event_datetime.weekday()]
            weekday_full = weekdays_full[event_datetime.weekday()]
            month = months[event_datetime.month - 1]
            day = event_datetime.day
            time_start = event_datetime.strftime("%H:%M")
            time_end = end_datetime.strftime("%H:%M")

            now = datetime.now(cal.timezone)
            emoji = cal.get_emoji_for_title(title)

            # Форматируем текст напоминаний
            reminder_text = _format_reminder_text(reminder_minutes)

            if recurrence:
                # Ответ для повторяющегося события
                recurrence_labels = {
                    "daily": "Каждый день",
                    "weekly": f"Каждый {weekday_full}",
                    "monthly": f"Каждый месяц {day}-го числа",
                }
                recurrence_label = recurrence_labels.get(recurrence, recurrence)

                response = f"🔄 {emoji} [{title}] Создано\n"
                response += f" · Повтор: {recurrence_label}\n"
                response += f" · Время: {time_start} - {time_end}\n"
                response += f" · Напоминание: {reminder_text}"
            else:
                # Ответ для обычного события
                if event_datetime.date() == now.date():
                    date_label = f"Сегодня ({weekday})"
                elif event_datetime.date() == (now + timedelta(days=1)).date():
                    date_label = f"Завтра ({weekday})"
                else:
                    date_label = f"({weekday}) {day} {month}"

                response = f"{emoji} [{title}] Добавлен\n"
                response += f" · Дата: {date_label}\n"
                response += f" · Время: {time_start} - {time_end}\n"
                if location:
                    response += f" · Место: {location}\n"
                response += f" · Напоминание: {reminder_text}"

            return response
        else:
            # Есть дата, но нет времени — сохраняем контекст и спрашиваем время
            if state is not None:
                await state.update_data(
                    pending_event={
                        "title": title,
                        "date": date_str,
                        "duration": duration,
                    }
                )
                await state.set_state(WaitingForEventTime.waiting)

            if date_str:
                return f"⏰ **{title}** на {date_str} — во сколько?"
            else:
                return f"⏰ **{title}** — когда запланировать?"

    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


async def handle_update_task(action: dict, telegram_id: int = None) -> str:
    """Обработка изменения задачи (время, длительность или напоминания)"""
    original_title = action.get("original_title", "")
    new_time = action.get("new_time")
    new_date = action.get("new_date")
    new_duration = action.get("new_duration")  # Новая длительность в минутах
    new_reminders = action.get("new_reminders")  # Новые напоминания [1440, 60]

    try:
        cal = await get_user_calendar_service(telegram_id) if telegram_id else None
        if cal is None:
            return "❌ Календарь не подключён. Используй /connect_calendar"

        # Ищем событие по названию
        event = cal.find_event_by_title(original_title)
        if not event:
            return f"🔍 Не нашёл событие «{original_title}»"

        event_title = event.get("summary", original_title)
        event_calendar_id = event.get("_calendar_id", "primary")  # Календарь события

        # Только меняем напоминания (без изменения времени)
        if new_reminders and not new_time and not new_duration:
            cal.update_event_reminders(event["id"], new_reminders, calendar_id=event_calendar_id)
            reminder_text = _format_reminder_text(new_reminders)
            return f"🔔 **{event_title}**\n · Напоминание: {reminder_text}"

        # Получаем текущее время события
        start = event.get("start", {})
        end = event.get("end", {})

        if "dateTime" in start:
            start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            start_dt = start_dt.astimezone(cal.timezone)
        else:
            return f"❌ Не могу изменить событие на весь день"

        if "dateTime" in end:
            end_dt = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
            current_duration = int((end_dt - start_dt).total_seconds() / 60)
        else:
            current_duration = 60

        # Определяем что меняем
        if new_duration:
            # Меняем только длительность, время остаётся
            duration = new_duration
            new_datetime = start_dt
            response_prefix = f"⏱️ **{event_title}**: {duration} мин"
        elif new_time:
            # Меняем время (и возможно дату)
            duration = current_duration
            date_to_use = new_date if new_date else "сегодня"
            new_datetime = cal.parse_datetime_from_text(date_to_use, new_time)
            response_prefix = f"📝 **{event_title}** перенесено"
        elif new_date:
            # Меняем только дату, время остаётся прежним
            duration = current_duration
            current_time = start_dt.strftime("%H:%M")
            new_datetime = cal.parse_datetime_from_text(new_date, current_time)
            response_prefix = f"📅 **{event_title}** перенесено"
        else:
            return f"❓ Что изменить в «{original_title}»?"

        new_end = new_datetime + timedelta(minutes=duration)

        # Проверяем конфликты (исключая само событие)
        conflicts = cal.check_conflicts(new_datetime, new_end, calendar_id=event_calendar_id, exclude_event_id=event["id"])
        conflict_warning = cal.format_conflict_warning(conflicts)

        # Обновляем событие (передаём calendar_id!)
        cal.update_event_time(event["id"], new_datetime, duration_minutes=duration, calendar_id=event_calendar_id)

        time_formatted = new_datetime.strftime("%H:%M")
        end_formatted = new_end.strftime("%H:%M")

        response = f"{response_prefix}\n📅 {time_formatted}–{end_formatted}"

        if conflict_warning:
            response += f"\n\n{conflict_warning}"

        return response

    except Exception as e:
        print(f"NameError: {e}")
        return f"❌ Ошибка: {str(e)[:50]}"


async def handle_delete_task(action: dict, telegram_id: int = None) -> str:
    """Обработка удаления задачи"""
    import logging
    logger = logging.getLogger(__name__)

    original_title = action.get("original_title", "")
    delete_all = action.get("delete_all", False)

    logger.info(f"🗑️ Удаление: user={telegram_id}, title='{original_title}', delete_all={delete_all}")

    try:
        cal = await get_user_calendar_service(telegram_id) if telegram_id else None
        if cal is None:
            logger.warning(f"🗑️ Календарь не подключён для {telegram_id}")
            return "❌ Календарь не подключён. Используй /connect_calendar"

        if delete_all:
            # Удаляем ВСЕ события с таким названием
            events = cal.find_all_events_by_title(original_title)
            logger.info(f"🗑️ find_all_events_by_title('{original_title}'): найдено {len(events)} событий")
            if not events:
                return f"🔍 Не нашёл событий «{original_title}»"

            deleted_count = 0
            title = events[0].get("summary", original_title)
            emoji = cal.get_emoji_for_title(title)

            for event in events:
                event_calendar_id = event.get("_calendar_id", "primary")
                if cal.delete_event(event["id"], calendar_id=event_calendar_id):
                    deleted_count += 1

            if deleted_count > 0:
                word = "событие" if deleted_count == 1 else "события" if 2 <= deleted_count <= 4 else "событий"
                return f"🗑 {emoji} [{title}] — удалено {deleted_count} {word}"
            else:
                return f"❌ Не удалось удалить события «{title}»"
        else:
            # Удаляем одно ближайшее событие (старое поведение)
            event = cal.find_event_by_title(original_title)
            logger.info(f"🗑️ find_event_by_title('{original_title}'): {'найдено' if event else 'НЕ найдено'}")
            if not event:
                return f"🔍 Не нашёл событие «{original_title}»"

            title = event.get("summary", original_title)
            emoji = cal.get_emoji_for_title(title)
            event_calendar_id = event.get("_calendar_id", "primary")

            success = cal.delete_event(event["id"], calendar_id=event_calendar_id)

            if success:
                return f"🗑 {emoji} [{title}] удалён"
            else:
                return f"❌ Не удалось удалить «{title}»"

    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


async def handle_list_tasks(action: dict, telegram_id: int = None) -> str:
    """Обработка запроса списка задач"""
    period = action.get("period", "today")

    cal = await get_user_calendar_service(telegram_id) if telegram_id else None
    if cal is None:
        return "❌ Календарь не подключён. Используй /connect_calendar"

    # Retry logic для сетевых ошибок
    for attempt in range(3):
        try:
            events = cal.get_events(period=period)
            return cal.format_events_list(events, period)
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1)  # Ждём перед retry
                continue
            break

    return f"❌ Ошибка соединения. Попробуй ещё раз."


async def handle_rename_task(action: dict, telegram_id: int = None) -> str:
    """Обработка переименования события"""
    original_title = action.get("original_title", "")
    new_title = action.get("new_title", "")

    if not new_title:
        return f"❓ Как назвать «{original_title}»?"

    try:
        cal = await get_user_calendar_service(telegram_id) if telegram_id else None
        if cal is None:
            return "❌ Календарь не подключён. Используй /connect_calendar"

        # Ищем событие по названию
        event = cal.find_event_by_title(original_title)
        if not event:
            return f"🔍 Не нашёл событие «{original_title}»"

        old_title = event.get("summary", original_title)
        event_calendar_id = event.get("_calendar_id", "primary")  # Календарь события
        old_emoji = cal.get_emoji_for_title(old_title)
        new_emoji = cal.get_emoji_for_title(new_title)

        # Переименовываем
        cal.rename_event(event["id"], new_title, calendar_id=event_calendar_id)

        return f"✏️ {old_emoji} [{old_title}] → {new_emoji} [{new_title}]"

    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


async def handle_find_free_slots(action: dict, telegram_id: int = None) -> str:
    """Обработка поиска свободных слотов"""
    date_str = action.get("date", "сегодня")
    min_duration = action.get("duration_minutes", 60)

    try:
        cal = await get_user_calendar_service(telegram_id) if telegram_id else None
        if cal is None:
            return "❌ Календарь не подключён. Используй /connect_calendar"
        slots = cal.find_free_slots(date_str, min_duration)
        return cal.format_free_slots(slots, date_str)

    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


async def handle_search_events(action: dict, telegram_id: int = None) -> str:
    """Обработка поиска событий"""
    query = action.get("query", "")
    period = action.get("period", "month")

    if not query:
        return "🔍 Укажи, что искать. Например: «найди все созвоны»"

    try:
        cal = await get_user_calendar_service(telegram_id) if telegram_id else None
        if cal is None:
            return "❌ Календарь не подключён. Используй /connect_calendar"
        events = cal.search_events(query, period)
        return cal.format_search_results(events, query)

    except Exception as e:
        return f"❌ Ошибка поиска: {str(e)[:50]}"


async def handle_set_reminder(action: dict, telegram_id: int = None) -> str:
    """Обработка установки отложенного напоминания"""
    from database.models import Reminder, User
    from sqlalchemy import select
    from services.limits_service import LimitsService
    import pytz
    import re

    message_text = action.get("message", "напоминание")

    if not telegram_id:
        return "❌ Не удалось определить пользователя"

    try:
        async with async_session() as session:
            # Получаем пользователя
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                return "❌ Пользователь не найден"

            # Проверяем лимит напоминаний
            limits = LimitsService(session)
            can_create, limit_error = await limits.can_create_reminder(user.id)
            if not can_create:
                return f"⚠️ {limit_error}"

            tz = pytz.timezone(config.TIMEZONE)
            now = datetime.now(tz)

            # Определяем время напоминания
            remind_at = None
            response_text = ""

            # Режим 1: Абсолютное время (date + time)
            if action.get("date") and action.get("time"):
                date_str = action["date"].lower()
                time_str = action["time"]

                # Парсим время
                time_match = re.match(r"(\d{1,2})[:\.](\d{2})", time_str)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                else:
                    return "❌ Не удалось разобрать время"

                # Парсим дату
                if date_str in ["сегодня", "today"]:
                    target_date = now.date()
                elif date_str in ["завтра", "tomorrow"]:
                    target_date = now.date() + timedelta(days=1)
                elif date_str in ["послезавтра"]:
                    target_date = now.date() + timedelta(days=2)
                else:
                    # Попробуем распарсить дату типа "20 января" или "2025-01-20"
                    months_ru = {
                        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
                        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
                        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12
                    }
                    date_match = re.match(r"(\d{1,2})\s+(\w+)", date_str)
                    if date_match:
                        day = int(date_match.group(1))
                        month_name = date_match.group(2).lower()
                        month = months_ru.get(month_name, now.month)
                        year = now.year
                        # Если дата уже прошла в этом году — берём следующий
                        try:
                            target_date = datetime(year, month, day).date()
                            if target_date < now.date():
                                target_date = datetime(year + 1, month, day).date()
                        except ValueError:
                            target_date = now.date() + timedelta(days=1)
                    else:
                        # ISO формат 2025-01-20
                        try:
                            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                        except ValueError:
                            target_date = now.date() + timedelta(days=1)

                remind_at = tz.localize(datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute)))

                # Формируем ответ
                if target_date == now.date():
                    date_text = "сегодня"
                elif target_date == now.date() + timedelta(days=1):
                    date_text = "завтра"
                else:
                    months_gen = ["января", "февраля", "марта", "апреля", "мая", "июня",
                                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]
                    date_text = f"{target_date.day} {months_gen[target_date.month - 1]}"

                response_text = f"⏰ Напомню {date_text} в {remind_at.strftime('%H:%M')}"

            # Режим 2: Относительное время (minutes)
            elif action.get("minutes"):
                minutes = action["minutes"]
                remind_at = now + timedelta(minutes=minutes)

                # Форматируем время для ответа
                if minutes >= 60:
                    hours = minutes // 60
                    mins = minutes % 60
                    if mins > 0:
                        time_str = f"{hours} ч {mins} мин"
                    else:
                        time_str = f"{hours} час" if hours == 1 else f"{hours} часа" if hours < 5 else f"{hours} часов"
                else:
                    time_str = f"{minutes} мин"

                response_text = f"⏰ Напомню через {time_str} (в {remind_at.strftime('%H:%M')})"

            else:
                # По умолчанию — через час
                remind_at = now + timedelta(hours=1)
                response_text = f"⏰ Напомню через час (в {remind_at.strftime('%H:%M')})"

            # Создаём напоминание
            reminder = Reminder(
                user_id=user.id,
                message=message_text,
                remind_at=remind_at,
                is_sent=False
            )
            session.add(reminder)

            # Увеличиваем счётчик напоминаний
            await limits.increment_reminder_usage(user.id)

            return response_text

    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


# --- НАСТРОЙКА РЕЖИМА РАБОТЫ БОТА ---

@router.message(Command("режим"))
async def command_working_hours(message: types.Message):
    """Показывает и позволяет изменить режим работы бота"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(message.from_user.id)

        start_time = user.morning_time or "08:00"
        end_time = user.evening_time or "22:00"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌅 Подъём", callback_data="wh_change_start"),
            InlineKeyboardButton(text="🌙 Отбой", callback_data="wh_change_end"),
        ]
    ])

    await message.answer(
        f"⏰ **Твой режим**\n\n"
        f"🌅 Просыпаешься: **{start_time}**\n"
        f"🌙 Ложишься: **{end_time}**\n\n"
        f"Напоминания приходят в это время.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "wh_change_start")
async def wh_change_start(call: types.CallbackQuery, state: FSMContext):
    """Изменение времени подъёма"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="06:00", callback_data="wh_start_06:00"),
            InlineKeyboardButton(text="07:00", callback_data="wh_start_07:00"),
            InlineKeyboardButton(text="08:00", callback_data="wh_start_08:00"),
        ],
        [
            InlineKeyboardButton(text="09:00", callback_data="wh_start_09:00"),
            InlineKeyboardButton(text="10:00", callback_data="wh_start_10:00"),
            InlineKeyboardButton(text="11:00", callback_data="wh_start_11:00"),
        ],
    ])

    await call.message.edit_text(
        "🌅 **Во сколько ты просыпаешься?**",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "wh_change_end")
async def wh_change_end(call: types.CallbackQuery, state: FSMContext):
    """Изменение времени отбоя"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="21:00", callback_data="wh_end_21:00"),
            InlineKeyboardButton(text="22:00", callback_data="wh_end_22:00"),
            InlineKeyboardButton(text="23:00", callback_data="wh_end_23:00"),
        ],
        [
            InlineKeyboardButton(text="00:00", callback_data="wh_end_00:00"),
            InlineKeyboardButton(text="01:00", callback_data="wh_end_01:00"),
        ],
    ])

    await call.message.edit_text(
        "🌙 **Во сколько ты ложишься спать?**",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("wh_init_start_"))
async def wh_init_start_time(call: types.CallbackQuery, state: FSMContext):
    """Начальная настройка: выбор времени начала"""
    from sqlalchemy import update
    from database.models import User
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    time_str = call.data.replace("wh_init_start_", "")

    # Сохраняем время начала
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == call.from_user.id)
            .values(morning_time=time_str)
        )
        await session.commit()

    # Сохраняем в state для показа в финальном сообщении
    await state.update_data(start_time=time_str)

    # Спрашиваем время конца
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="20:00", callback_data="wh_init_end_20:00"),
            InlineKeyboardButton(text="21:00", callback_data="wh_init_end_21:00"),
            InlineKeyboardButton(text="22:00 ✓", callback_data="wh_init_end_22:00"),
        ],
        [
            InlineKeyboardButton(text="23:00", callback_data="wh_init_end_23:00"),
            InlineKeyboardButton(text="00:00", callback_data="wh_init_end_00:00"),
        ],
    ])

    await call.message.edit_text(
        f"✅ Подъём: **{time_str}**\n\n"
        "🌙 Во сколько ты обычно ложишься спать?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("wh_init_end_"))
async def wh_init_end_time(call: types.CallbackQuery, state: FSMContext):
    """Начальная настройка: выбор времени конца и завершение"""
    from sqlalchemy import update
    from database.models import User
    from services.google_oauth_service import GoogleOAuthService

    time_str = call.data.replace("wh_init_end_", "")

    # Сохраняем время конца
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == call.from_user.id)
            .values(evening_time=time_str)
        )
        await session.commit()

        # Получаем полные данные пользователя
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)
        start_time = user.morning_time or "08:00"
        calendar_connected = user.calendar_connected

    await state.clear()

    await call.message.edit_text(
        f"✅ Режим настроен: {start_time} — {time_str}\n\n"
        f"Изменить можно командой /режим"
    )

    # Задержка перед следующим шагом
    await asyncio.sleep(1.5)

    # Предлагаем выбрать: подключить календарь или настроить VPN
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard_buttons = []

    # Кнопка календаря (если OAuth настроен)
    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        keyboard_buttons.append([
            InlineKeyboardButton(text="📅 Подключить календарь", callback_data="onboard_calendar")
        ])

    # Кнопка VPN
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔐 Попробовать VPN бесплатно", callback_data="onboard_vpn")
    ])

    # Кнопка пропустить
    keyboard_buttons.append([
        InlineKeyboardButton(text="⏭ Пропустить", callback_data="onboard_skip")
    ])

    await call.message.answer(
        "🚀 Что настроим?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    )


@router.callback_query(F.data == "onboard_calendar")
async def onboard_calendar(call: types.CallbackQuery):
    """Онбординг: подключение календаря"""
    from services.google_oauth_service import GoogleOAuthService

    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        oauth = GoogleOAuthService()
        auth_url = oauth.create_auth_url(call.from_user.id)

        await call.message.edit_text(
            "📅 **Подключи Google Calendar**\n\n"
            "Перейди по ссылке и разреши доступ к календарю:\n\n"
            f"[👉 Подключить календарь]({auth_url})\n\n"
            "После подключения я смогу:\n"
            "• Показывать твои события\n"
            "• Создавать новые встречи\n"
            "• Напоминать о предстоящих делах",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        await call.answer("Календарь временно недоступен", show_alert=True)


@router.callback_query(F.data == "onboard_vpn")
async def onboard_vpn(call: types.CallbackQuery):
    """Онбординг: настройка VPN"""
    # Перенаправляем в меню VPN
    await call.message.edit_text(
        "🔐 **Защищённый туннель**\n\n"
        "Перехожу в настройки VPN...",
        parse_mode="Markdown"
    )

    # Симулируем команду /tunnel
    from handlers.tunnel import cmd_tunnel
    await cmd_tunnel(call.message)


@router.callback_query(F.data == "onboard_skip")
async def onboard_skip(call: types.CallbackQuery):
    """Онбординг: пропустить настройку"""
    await call.message.edit_text(
        "✅ Готово! Ты всегда можешь настроить эти функции позже:\n\n"
        "📅 /connect\\_calendar — подключить Google Calendar\n"
        "🔐 /tunnel — настроить VPN",
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "onboard_mode_ready")
async def onboard_mode_ready(call: types.CallbackQuery):
    """Онбординг: пользователь готов настроить режим"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="06:00", callback_data="wh_init_start_06:00"),
            InlineKeyboardButton(text="07:00", callback_data="wh_init_start_07:00"),
            InlineKeyboardButton(text="08:00 ✓", callback_data="wh_init_start_08:00"),
        ],
        [
            InlineKeyboardButton(text="09:00", callback_data="wh_init_start_09:00"),
            InlineKeyboardButton(text="10:00", callback_data="wh_init_start_10:00"),
            InlineKeyboardButton(text="11:00", callback_data="wh_init_start_11:00"),
        ],
    ])

    await call.message.edit_text(
        "⏰ С какого времени тебе можно писать?\n\n"
        "Выбери время, когда ты обычно просыпаешься:",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "onboard_mode_skip")
async def onboard_mode_skip(call: types.CallbackQuery):
    """Онбординг: пропустить настройку режима, перейти к следующему шагу"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    await call.message.edit_text(
        "👌 Ок, оставлю стандартный режим (08:00 — 22:00).\n"
        "Изменить можно в любой момент: /режим"
    )

    # Задержка перед следующим шагом
    await asyncio.sleep(1.5)

    # Предлагаем выбрать: подключить календарь или настроить VPN
    keyboard_buttons = []

    # Кнопка календаря (если OAuth настроен)
    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        keyboard_buttons.append([
            InlineKeyboardButton(text="📅 Подключить календарь", callback_data="onboard_calendar")
        ])

    # Кнопка VPN
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔐 Попробовать VPN бесплатно", callback_data="onboard_vpn")
    ])

    # Кнопка пропустить
    keyboard_buttons.append([
        InlineKeyboardButton(text="⏭ Пропустить", callback_data="onboard_skip")
    ])

    await call.message.answer(
        "🚀 Что настроим?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    )


@router.callback_query(F.data.startswith("wh_start_"))
async def wh_set_start_time(call: types.CallbackQuery):
    """Установка времени начала"""
    from sqlalchemy import update
    from database.models import User

    time_str = call.data.replace("wh_start_", "")

    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == call.from_user.id)
            .values(morning_time=time_str)
        )
        await session.commit()

        # Получаем обновлённые данные
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)
        end_time = user.evening_time or "22:00"

    await call.message.edit_text(
        f"✅ Готово! Теперь я начинаю писать с **{time_str}**\n\n"
        f"Текущий режим: {time_str} — {end_time}",
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("wh_end_"))
async def wh_set_end_time(call: types.CallbackQuery):
    """Установка времени конца"""
    from sqlalchemy import update
    from database.models import User

    time_str = call.data.replace("wh_end_", "")

    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == call.from_user.id)
            .values(evening_time=time_str)
        )
        await session.commit()

        # Получаем обновлённые данные
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)
        start_time = user.morning_time or "08:00"

    await call.message.edit_text(
        f"✅ Готово! Теперь я заканчиваю писать в **{time_str}**\n\n"
        f"Текущий режим: {start_time} — {time_str}",
        parse_mode="Markdown"
    )


# === БРОНИРОВАНИЕ ВСТРЕЧ ===

BOOKING_BASE_URL = "https://djarvis.vincora.ru/book"


@router.message(Command("booking"))
async def command_booking(message: types.Message, state: FSMContext):
    """Настройка бронирования встреч"""
    telegram_id = message.from_user.id

    async with async_session() as session:
        # Проверяем подключение календаря
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(telegram_id)

        if not user.calendar_connected:
            await message.answer(
                "Для бронирования нужно подключить Google Calendar.\n\n"
                "Используй /connect_calendar"
            )
            return

        # Получаем существующую ссылку (если есть)
        from booking.service import BookingService
        booking_service = BookingService(session)
        links = await booking_service.get_user_booking_links(user.id)
        active_link = next((l for l in links if l.is_active), None)

        if active_link:
            # Есть активная ссылка — показываем её и предлагаем перенастроить
            schedule = await booking_service.get_user_schedule(user.id)
            work_start = "09:00"
            work_end = "18:00"
            days_text = "Пн-Пт"

            if schedule:
                if schedule.working_hours and "monday" in schedule.working_hours:
                    work_start = schedule.working_hours["monday"].get("start", "09:00")
                    work_end = schedule.working_hours["monday"].get("end", "18:00")
                available = schedule.available_days.split(",")
                days_map = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
                days_text = ", ".join([days_map[int(d)] for d in available if d])

            url = f"{BOOKING_BASE_URL}/{active_link.slug}"

            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Перенастроить", callback_data="booking_setup")]
            ])

            await message.answer(
                f"**Твоя ссылка для записи:**\n"
                f"{url}\n\n"
                f"**Настройки:**\n"
                f"• Длительность: {active_link.duration_minutes} мин\n"
                f"• Рабочие часы: {work_start} — {work_end}\n"
                f"• Дни: {days_text}\n\n"
                f"Поделись ссылкой — люди смогут записаться на созвон.",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            # Нет ссылки — начинаем настройку
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Настроить", callback_data="booking_setup")]
            ])
            await message.answer(
                "**Запись на встречу**\n\n"
                "Создай ссылку, чтобы люди могли записываться на созвон в твой календарь.\n\n"
                "Я покажу им свободные слоты на основе твоего Google Calendar.",
                parse_mode="Markdown",
                reply_markup=keyboard
            )


@router.callback_query(F.data == "booking_setup")
async def booking_setup_start(call: types.CallbackQuery, state: FSMContext):
    """Начало настройки бронирования"""
    await call.answer()

    # Проверяем лимит ссылок бронирования
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(call.from_user.id)

        from services.limits_service import LimitsService
        limits = LimitsService(session)
        can_create, limit_error = await limits.can_create_booking_link(user.id)

        if not can_create:
            await call.message.edit_text(
                f"⚠️ {limit_error}\n\n"
                "Перейдите на более высокий тариф в разделе /tunnel → Тарифы",
                parse_mode="Markdown"
            )
            return

    await state.set_state(BookingStates.waiting_for_duration)

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="15 мин", callback_data="booking_dur_15"),
            types.InlineKeyboardButton(text="30 мин", callback_data="booking_dur_30"),
        ],
        [
            types.InlineKeyboardButton(text="45 мин", callback_data="booking_dur_45"),
            types.InlineKeyboardButton(text="60 мин", callback_data="booking_dur_60"),
        ],
    ])

    await call.message.edit_text(
        "**Шаг 1/4: Длительность встречи**\n\n"
        "Сколько длится одна встреча?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("booking_dur_"))
async def booking_duration_selected(call: types.CallbackQuery, state: FSMContext):
    """Выбрана длительность"""
    await call.answer()

    duration = int(call.data.replace("booking_dur_", ""))
    await state.update_data(booking_duration=duration)
    await state.set_state(BookingStates.waiting_for_days_ahead)

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="1 неделя", callback_data="booking_days_7"),
            types.InlineKeyboardButton(text="2 недели", callback_data="booking_days_14"),
        ],
        [
            types.InlineKeyboardButton(text="1 месяц", callback_data="booking_days_30"),
            types.InlineKeyboardButton(text="2 месяца", callback_data="booking_days_60"),
        ],
    ])

    await call.message.edit_text(
        f"**Шаг 2/4: Период бронирования**\n\n"
        f"Длительность: {duration} мин\n\n"
        "На какой период открыть запись?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("booking_days_"))
async def booking_days_ahead_selected(call: types.CallbackQuery, state: FSMContext):
    """Выбран период бронирования"""
    await call.answer()

    days = int(call.data.replace("booking_days_", ""))
    await state.update_data(booking_days_ahead=days)
    await state.set_state(BookingStates.waiting_for_work_start)

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="08:00", callback_data="booking_start_08"),
            types.InlineKeyboardButton(text="09:00", callback_data="booking_start_09"),
            types.InlineKeyboardButton(text="10:00", callback_data="booking_start_10"),
        ],
        [
            types.InlineKeyboardButton(text="11:00", callback_data="booking_start_11"),
            types.InlineKeyboardButton(text="12:00", callback_data="booking_start_12"),
        ],
    ])

    data = await state.get_data()
    duration = data.get("booking_duration", 30)
    days_text = {7: "1 неделя", 14: "2 недели", 30: "1 месяц", 60: "2 месяца"}.get(days, f"{days} дн.")

    await call.message.edit_text(
        f"**Шаг 3/4: Рабочие часы**\n\n"
        f"Длительность: {duration} мин\n"
        f"Период: {days_text}\n\n"
        "С какого времени принимать записи?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("booking_start_"))
async def booking_work_start_selected(call: types.CallbackQuery, state: FSMContext):
    """Выбрано начало рабочего дня"""
    await call.answer()

    hour = int(call.data.replace("booking_start_", ""))
    await state.update_data(booking_work_start=f"{hour:02d}:00")
    await state.set_state(BookingStates.waiting_for_work_end)

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="17:00", callback_data="booking_end_17"),
            types.InlineKeyboardButton(text="18:00", callback_data="booking_end_18"),
            types.InlineKeyboardButton(text="19:00", callback_data="booking_end_19"),
        ],
        [
            types.InlineKeyboardButton(text="20:00", callback_data="booking_end_20"),
            types.InlineKeyboardButton(text="21:00", callback_data="booking_end_21"),
        ],
    ])

    data = await state.get_data()
    duration = data.get("booking_duration", 30)
    days = data.get("booking_days_ahead", 14)
    days_text = {7: "1 неделя", 14: "2 недели", 30: "1 месяц", 60: "2 месяца"}.get(days, f"{days} дн.")

    await call.message.edit_text(
        f"**Шаг 4/4: Конец рабочего дня**\n\n"
        f"Длительность: {duration} мин\n"
        f"Период: {days_text}\n"
        f"Начало: {hour:02d}:00\n\n"
        "До какого времени принимать записи?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("booking_end_"))
async def booking_work_end_selected(call: types.CallbackQuery, state: FSMContext):
    """Выбран конец рабочего дня — создаём/обновляем ссылку"""
    await call.answer()

    hour = int(call.data.replace("booking_end_", ""))
    work_end = f"{hour:02d}:00"

    data = await state.get_data()
    duration = data.get("booking_duration", 30)
    work_start = data.get("booking_work_start", "09:00")
    days_ahead = data.get("booking_days_ahead", 14)

    telegram_id = call.from_user.id

    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(telegram_id)

        from booking.service import BookingService
        booking_service = BookingService(session)

        # Деактивируем старые ссылки
        old_links = await booking_service.get_user_booking_links(user.id)
        for old_link in old_links:
            if old_link.is_active:
                await booking_service.deactivate_booking_link(old_link.id)

        # Создаём новую ссылку
        link = await booking_service.create_booking_link(
            user_id=user.id,
            title="Встреча",
            duration_minutes=duration,
            max_days_ahead=days_ahead,
        )

        # Сохраняем настройки расписания
        working_hours = {}
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            working_hours[day] = {"start": work_start, "end": work_end}

        await booking_service.update_user_schedule(
            user_id=user.id,
            working_hours=working_hours,
            available_days="0,1,2,3,4",  # Пн-Пт
        )

    await state.clear()

    days_text = {7: "1 неделя", 14: "2 недели", 30: "1 месяц", 60: "2 месяца"}.get(days_ahead, f"{days_ahead} дн.")
    url = f"{BOOKING_BASE_URL}/{link.slug}"
    await call.message.edit_text(
        f"**Готово!**\n\n"
        f"Твоя ссылка для записи:\n{url}\n\n"
        f"**Настройки:**\n"
        f"• Длительность: {duration} мин\n"
        f"• Период: {days_text}\n"
        f"• Рабочие часы: {work_start} — {work_end}\n"
        f"• Дни: Пн-Пт\n\n"
        "Поделись ссылкой — люди смогут записаться на созвон.",
        parse_mode="Markdown"
    )


def register_handlers_user(dp):
    """Регистрация роутера"""
    dp.include_router(router)
