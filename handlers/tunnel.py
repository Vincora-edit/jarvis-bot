"""
Хендлеры для модуля VPN/Туннель.
"""
import logging
from datetime import datetime, timedelta
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from database import async_session
from database.models import Subscription, PromoCode, PromoCodeUsage, User, TunnelKey
from services.memory_service import MemoryService
from services.vpn_service import TunnelService, VPN_PLAN_LIMITS
from services.limits_service import LimitsService
from services.plans import get_plan_name

# Алиас для обратной совместимости
PLAN_LIMITS = VPN_PLAN_LIMITS
from keyboards.tunnel_kb import (
    tunnel_menu_keyboard,
    plans_keyboard,
    plan_periods_keyboard,
    devices_keyboard,
    back_to_menu_keyboard,
    confirm_revoke_keyboard,
    promo_keyboard
)

logger = logging.getLogger(__name__)
router = Router()


# Промокоды теперь хранятся в БД (таблица promo_codes)


# === FSM для промокода ===
class PromoStates(StatesGroup):
    waiting_for_code = State()


# === FSM для переименования устройства ===
class RenameStates(StatesGroup):
    waiting_for_name = State()


# === FSM для создания устройства ===
class AddDeviceStates(StatesGroup):
    waiting_for_name = State()


# === КОМАНДА /tunnel ===

@router.message(Command("tunnel"))
@router.message(F.text == "🔒 Туннель")
async def cmd_tunnel(message: types.Message):
    """Главное меню туннеля"""
    try:
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(message.from_user.id)

            tunnel_service = TunnelService(session)
            limits_service = LimitsService(session)

            # Проверяем VPN доступ через LimitsService
            can_vpn, vpn_status, vpn_devices = await limits_service.can_use_vpn(user.id)

            plan = await tunnel_service.get_user_plan(user.id)
            keys_count = await tunnel_service.get_keys_count(user.id)

            # Определяем есть ли активная подписка или триал
            has_subscription = can_vpn and vpn_status not in ["trial"]

            if can_vpn:
                # Получаем дату окончания
                sub = await tunnel_service.get_user_subscription(user.id)
                expire_text = ""

                # Проверяем триал
                if vpn_status.startswith("trial_active:"):
                    days_left = int(vpn_status.split(":")[1])
                    expire_text = f"\n🎁 Пробный период: {days_left} дн."
                    plan_display = "ТРИАЛ"
                elif sub and sub.expires_at:
                    days_left = (sub.expires_at - datetime.utcnow()).days
                    expire_text = f"\n📅 До: {sub.expires_at.strftime('%d.%m.%Y')} ({days_left} дн.)"
                    plan_display = get_plan_name(plan).upper()
                else:
                    plan_display = get_plan_name(plan).upper()

                text = (
                    f"🔐 *Защищённый туннель*\n\n"
                    f"📊 Ваш план: *{plan_display}*\n"
                    f"📱 Макс. устройств: {vpn_devices}{expire_text}\n\n"
                    f"Выберите действие:"
                )
            else:
                # Проверяем доступен ли триал
                trial_text = ""
                if not user.vpn_trial_used:
                    trial_text = "\n\n🎁 *Попробуйте бесплатно 7 дней!*"

                text = (
                    f"🔐 *Защищённый туннель*\n\n"
                    f"Безопасный и быстрый доступ к интернету.\n\n"
                    f"✅ Безлимитный трафик\n"
                    f"✅ Высокая скорость\n"
                    f"✅ Серверы в Европе\n"
                    f"✅ Поддержка всех устройств{trial_text}\n\n"
                    f"Оформите подписку, чтобы начать:"
                )

            # Используем vpn_devices из LimitsService (учитывает триал и подписку)
            # Если can_vpn=True, то vpn_devices содержит правильное количество устройств
            max_keys = vpn_devices if can_vpn else 0

            await message.answer(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=tunnel_menu_keyboard(
                    has_subscription=can_vpn,
                    keys_count=keys_count,
                    max_keys=max_keys,
                    show_trial=not user.vpn_trial_used and not can_vpn
                )
            )
    except Exception as e:
        logger.error(f"Error in cmd_tunnel: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


# === КОМАНДА /subscription ===

@router.message(Command("subscription"))
@router.message(Command("подписка"))
async def cmd_subscription(message: types.Message):
    """Информация о подписке на Jarvis"""
    try:
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(message.from_user.id)

            tunnel_service = TunnelService(session)
            limits_service = LimitsService(session)

            plan = await tunnel_service.get_user_plan(user.id)
            sub = await tunnel_service.get_user_subscription(user.id)

            can_vpn, vpn_status, vpn_devices = await limits_service.can_use_vpn(user.id)

            # Формируем информацию о текущем плане
            from services.plans import get_plan_limits, PLAN_NAMES

            limits = get_plan_limits(plan)
            plan_name = PLAN_NAMES.get(plan, plan.capitalize())

            # Статус подписки
            if vpn_status.startswith("trial_active:"):
                days_left = int(vpn_status.split(":")[1])
                status_text = f"🎁 *Пробный период*\nОсталось: {days_left} дн."
            elif sub and sub.expires_at:
                days_left = (sub.expires_at - datetime.utcnow()).days
                status_text = f"✅ *Активна до* {sub.expires_at.strftime('%d.%m.%Y')} ({days_left} дн.)"
            elif plan == "free":
                status_text = "📦 *Бесплатный план*"
            else:
                status_text = f"✅ *План: {plan_name}*"

            # Лимиты плана
            ai_limit = "безлимит" if limits.ai_requests_per_day == 0 else str(limits.ai_requests_per_day)
            habits_limit = "безлимит" if limits.habits_max == 0 else str(limits.habits_max)
            reminders_limit = "безлимит" if limits.reminders_per_day == 0 else str(limits.reminders_per_day)

            text = (
                f"📊 *Ваша подписка на Jarvis*\n\n"
                f"{status_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"*Возможности плана:*\n"
                f"🤖 AI запросов/день: {ai_limit}\n"
                f"✅ Привычек: {habits_limit}\n"
                f"🔔 Напоминаний/день: {reminders_limit}\n"
                f"🔐 VPN устройств: {limits.vpn_devices or 'нет'}\n"
                f"📊 Аналитика: {'да' if limits.analytics_enabled else 'нет'}\n"
            )

            # Кнопки
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            buttons = []
            if plan == "free" or (sub and sub.expires_at and (sub.expires_at - datetime.utcnow()).days < 30):
                buttons.append([
                    InlineKeyboardButton(text="💳 Улучшить план", callback_data="tunnel:plans")
                ])

            if not user.vpn_trial_used and plan == "free":
                buttons.append([
                    InlineKeyboardButton(text="🎁 7 дней бесплатно", callback_data="tunnel:trial")
                ])

            buttons.append([
                InlineKeyboardButton(text="🎁 Ввести промокод", callback_data="tunnel:promo")
            ])

            await message.answer(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
            )
    except Exception as e:
        logger.error(f"Error in cmd_subscription: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


# === CALLBACK: МЕНЮ ===

@router.callback_query(F.data == "tunnel:menu")
async def callback_tunnel_menu(callback: types.CallbackQuery):
    """Вернуться в главное меню туннеля"""
    try:
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            tunnel_service = TunnelService(session)
            limits_service = LimitsService(session)

            # Используем LimitsService для корректной проверки (включая триал)
            can_vpn, vpn_status, vpn_devices = await limits_service.can_use_vpn(user.id)

            plan = await tunnel_service.get_user_plan(user.id)
            keys_count = await tunnel_service.get_keys_count(user.id)

            max_keys = vpn_devices if can_vpn else 0

            if can_vpn:
                sub = await tunnel_service.get_user_subscription(user.id)
                expire_text = ""

                # Определяем отображение плана
                if vpn_status.startswith("trial_active:"):
                    days_left = int(vpn_status.split(":")[1])
                    expire_text = f"\n🎁 Пробный период: {days_left} дн."
                    plan_display = "ТРИАЛ"
                elif sub and sub.expires_at:
                    days_left = (sub.expires_at - datetime.utcnow()).days
                    expire_text = f"\n📅 До: {sub.expires_at.strftime('%d.%m.%Y')} ({days_left} дн.)"
                    plan_display = get_plan_name(plan).upper()
                else:
                    plan_display = get_plan_name(plan).upper()

                text = (
                    f"🔐 *Защищённый туннель*\n\n"
                    f"📊 Ваш план: *{plan_display}*\n"
                    f"📱 Устройств: {keys_count}/{max_keys}{expire_text}\n\n"
                    f"Выберите действие:"
                )
            else:
                text = (
                    f"🔐 *Защищённый туннель*\n\n"
                    f"Безопасный и быстрый доступ к интернету.\n\n"
                    f"Оформите подписку, чтобы начать:"
                )

            await callback.message.edit_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=tunnel_menu_keyboard(can_vpn, keys_count, max_keys)
            )
    except Exception as e:
        logger.error(f"Error in callback_tunnel_menu: {e}")
    await callback.answer()


# === CALLBACK: ПОЛУЧИТЬ КЛЮЧ ===

@router.callback_query(F.data == "tunnel:get_key")
async def callback_get_key(callback: types.CallbackQuery):
    """Получить VPN ключ"""
    try:
        await callback.message.edit_text("⏳ Подключаюсь к серверу...")

        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            tunnel_service = TunnelService(session)

            # Проверяем можно ли создать/получить ключ
            can_create, error, max_keys = await tunnel_service.can_create_key(user.id)
            if not can_create:
                await callback.message.edit_text(
                    f"❌ {error}",
                    reply_markup=back_to_menu_keyboard()
                )
                await callback.answer()
                return

            # Создаём или обновляем ключ (create_key сам обновит существующий)
            sub_url, error = await tunnel_service.create_key(
                user_id=user.id,
                telegram_id=callback.from_user.id,
                full_name=callback.from_user.full_name or "User",
                device_name="Device"
            )

            if sub_url:
                text = (
                    f"🔑 *Ваш ключ:*\n\n"
                    f"Скопируй ссылку ниже и вставь в приложение Happ:\n\n"
                    f"`{sub_url}`\n\n"
                    f"_Нажми на ссылку, чтобы скопировать._"
                )
                await callback.message.edit_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_to_menu_keyboard()
                )
            else:
                await callback.message.edit_text(
                    f"❌ Ошибка: {error}\n\nПопробуйте позже или обратитесь в поддержку.",
                    reply_markup=back_to_menu_keyboard()
                )
    except Exception as e:
        logger.error(f"Error in callback_get_key: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка при создании ключа. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )
    await callback.answer()


# === CALLBACK: ДОБАВИТЬ УСТРОЙСТВО ===

@router.callback_query(F.data == "tunnel:add_device")
async def callback_add_device(callback: types.CallbackQuery, state: FSMContext):
    """Спросить название устройства перед созданием ключа"""
    try:
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            tunnel_service = TunnelService(session)
            limits_service = LimitsService(session)

            # Проверяем может ли пользователь создать ключ
            can_vpn, vpn_status, vpn_devices = await limits_service.can_use_vpn(user.id)

            # Если нет подписки и нет триала — показываем ошибку
            if not can_vpn and vpn_status != "trial":
                await callback.message.edit_text(
                    "❌ У вас нет активной подписки VPN.\n\n"
                    "Оформите подписку или активируйте пробный период.",
                    reply_markup=back_to_menu_keyboard()
                )
                await callback.answer()
                return

            # Для пользователей с подпиской — проверяем лимит
            if vpn_status != "trial":
                can_create, error, max_keys = await tunnel_service.can_create_key(user.id)
                if not can_create:
                    await callback.message.edit_text(
                        f"❌ {error}",
                        reply_markup=back_to_menu_keyboard()
                    )
                    await callback.answer()
                    return

            # Сохраняем в state, что это триал (если да)
            await state.update_data(is_trial_activation=(vpn_status == "trial"))
            await state.set_state(AddDeviceStates.waiting_for_name)

            await callback.message.edit_text(
                "📱 *Новое устройство*\n\n"
                "Введите название для устройства:\n"
                "_Например: iPhone, MacBook, Рабочий ПК_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_to_menu_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in callback_add_device: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )
    await callback.answer()


@router.message(AddDeviceStates.waiting_for_name)
async def process_add_device_name(message: types.Message, state: FSMContext):
    """Создать ключ с указанным названием устройства"""
    try:
        device_name = message.text.strip()[:50]  # Ограничение 50 символов

        if not device_name:
            await message.answer("❌ Название не может быть пустым. Попробуйте ещё раз.")
            return

        data = await state.get_data()
        is_trial = data.get("is_trial_activation", False)

        await message.answer("⏳ Создаю ключ...")

        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(message.from_user.id)

            tunnel_service = TunnelService(session)
            limits_service = LimitsService(session)

            # Если это активация триала
            if is_trial:
                success, trial_message = await limits_service.activate_vpn_trial(user.id)
                if not success:
                    await message.answer(
                        f"❌ {trial_message}",
                        reply_markup=back_to_menu_keyboard()
                    )
                    await state.clear()
                    return

                # Обновляем user из БД
                await session.refresh(user)

                # Создаём подписку для триала
                trial_sub = Subscription(
                    user_id=user.id,
                    plan="free_trial",
                    status="active",
                    expires_at=user.vpn_trial_expires
                )
                session.add(trial_sub)
                await session.commit()

            # Создаём ключ
            sub_url, error = await tunnel_service.create_key(
                user_id=user.id,
                telegram_id=message.from_user.id,
                full_name=message.from_user.full_name or "User",
                device_name=device_name
            )

            if sub_url:
                if is_trial:
                    text = (
                        f"🎉 *Пробный период активирован!*\n\n"
                        f"У вас есть *7 дней* бесплатного VPN.\n"
                        f"Устройство: *{device_name}*\n\n"
                        f"Скопируй ссылку ниже и вставь в приложение Happ:\n\n"
                        f"`{sub_url}`\n\n"
                        f"_Нажми на ссылку, чтобы скопировать._"
                    )
                else:
                    text = (
                        f"🔑 *Ключ создан!*\n\n"
                        f"Устройство: *{device_name}*\n\n"
                        f"Скопируй ссылку ниже и вставь в приложение Happ:\n\n"
                        f"`{sub_url}`\n\n"
                        f"_Нажми на ссылку, чтобы скопировать._"
                    )
                await message.answer(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_to_menu_keyboard()
                )
            else:
                await message.answer(
                    f"❌ Ошибка: {error}\n\nПопробуйте позже.",
                    reply_markup=back_to_menu_keyboard()
                )

        await state.clear()
    except Exception as e:
        logger.error(f"Error in process_add_device_name: {e}")
        await message.answer(
            "❌ Произошла ошибка при создании ключа. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )
        await state.clear()


# === CALLBACK: СТАТИСТИКА ===

@router.callback_query(F.data == "tunnel:stats")
async def callback_stats(callback: types.CallbackQuery):
    """Показать статистику использования"""
    try:
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            tunnel_service = TunnelService(session)
            stats = await tunnel_service.get_user_stats(callback.from_user.id)

            if not stats:
                await callback.answer("У вас пока нет активного подключения", show_alert=True)
                return

            text = (
                f"📊 *Статистика*\n\n"
                f"Статус: {stats['status_emoji']} {stats['status'].capitalize()}\n"
                f"📈 Трафик: *{stats['traffic_text']}*\n"
                f"📅 Действует до: *{stats['expire_text']}*"
            )

            await callback.message.edit_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_to_menu_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in callback_stats: {e}")
    await callback.answer()


# === CALLBACK: ТРИАЛ ===

@router.callback_query(F.data == "tunnel:trial")
async def callback_trial(callback: types.CallbackQuery):
    """Активировать VPN триал на 7 дней"""
    try:
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            limits_service = LimitsService(session)

            # Активируем триал
            success, message = await limits_service.activate_vpn_trial(user.id)

            if not success:
                await callback.message.edit_text(
                    f"❌ {message}",
                    reply_markup=back_to_menu_keyboard()
                )
                await callback.answer()
                return

            # Триал активирован, создаём VPN ключ
            await callback.message.edit_text("⏳ Активирую пробный период...")

            tunnel_service = TunnelService(session)

            # Создаём временную подписку для триала
            trial_sub = Subscription(
                user_id=user.id,
                plan="free_trial",
                status="active",
                expires_at=user.vpn_trial_expires
            )
            session.add(trial_sub)
            await session.commit()

            # Создаём ключ в Marzban
            sub_url, error = await tunnel_service.create_key(
                user_id=user.id,
                telegram_id=callback.from_user.id,
                full_name=callback.from_user.full_name or "User",
                device_name="Trial Device"
            )

            if sub_url:
                text = (
                    f"🎉 *Пробный период активирован!*\n\n"
                    f"У вас есть *7 дней* бесплатного VPN.\n\n"
                    f"Скопируй ссылку ниже и вставь в приложение Happ:\n\n"
                    f"`{sub_url}`\n\n"
                    f"_Нажми на ссылку, чтобы скопировать._"
                )
                await callback.message.edit_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_to_menu_keyboard()
                )
            else:
                await callback.message.edit_text(
                    f"⚠️ Триал активирован, но не удалось создать ключ: {error}\n\n"
                    f"Перейдите в меню и нажмите «Получить ключ».",
                    reply_markup=back_to_menu_keyboard()
                )
    except Exception as e:
        logger.error(f"Error in callback_trial: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )
    await callback.answer()


# === CALLBACK: МОИ УСТРОЙСТВА ===

@router.callback_query(F.data == "tunnel:devices")
async def callback_devices(callback: types.CallbackQuery):
    """Список устройств"""
    try:
        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            tunnel_service = TunnelService(session)
            keys = await tunnel_service.get_user_keys(user.id)

            if not keys:
                await callback.answer("У вас нет активных устройств", show_alert=True)
                return

            # Проверяем можно ли добавить ещё
            can_add, _, max_keys = await tunnel_service.can_create_key(user.id)

            text = (
                f"📱 *Мои устройства* ({len(keys)}/{max_keys})\n\n"
                f"Нажмите на устройство чтобы скопировать ключ.\n"
                f"Нажмите ❌ чтобы удалить."
            )

            await callback.message.edit_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=devices_keyboard(keys, can_add=can_add)
            )
    except Exception as e:
        logger.error(f"Error in callback_devices: {e}")
    await callback.answer()


# === CALLBACK: ПОКАЗАТЬ КЛЮЧ УСТРОЙСТВА ===

@router.callback_query(F.data.startswith("tunnel:show_key:"))
async def callback_show_key(callback: types.CallbackQuery):
    """Показать ключ конкретного устройства"""
    try:
        key_id = int(callback.data.split(":")[2])

        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            # SECURITY: Проверяем что ключ принадлежит пользователю
            result = await session.execute(
                select(TunnelKey).where(
                    TunnelKey.id == key_id,
                    TunnelKey.user_id == user.id
                )
            )
            key = result.scalar_one_or_none()

            if not key:
                await callback.answer("Ключ не найден", show_alert=True)
                return

            text = (
                f"🔑 *{key.device_name}*\n\n"
                f"Скопируй ссылку ниже и вставь в приложение Happ:\n\n"
                f"`{key.subscription_url}`\n\n"
                f"_Нажми на ссылку, чтобы скопировать._"
            )

            await callback.message.edit_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_to_menu_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in callback_show_key: {e}")
    await callback.answer()


# === CALLBACK: УДАЛИТЬ КЛЮЧ ===

@router.callback_query(F.data.startswith("tunnel:revoke:"))
async def callback_revoke(callback: types.CallbackQuery):
    """Подтверждение удаления ключа"""
    try:
        key_id = int(callback.data.split(":")[2])
        await callback.message.edit_text(
            "⚠️ *Удалить этот ключ?*\n\nУстройство потеряет доступ к туннелю.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_revoke_keyboard(key_id)
        )
    except Exception as e:
        logger.error(f"Error in callback_revoke: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("tunnel:revoke_confirm:"))
async def callback_revoke_confirm(callback: types.CallbackQuery):
    """Удаление ключа"""
    try:
        key_id = int(callback.data.split(":")[2])

        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            tunnel_service = TunnelService(session)
            success = await tunnel_service.revoke_key(user.id, key_id)

            if success:
                await callback.answer("✅ Ключ удалён")
                keys = await tunnel_service.get_user_keys(user.id)
                if keys:
                    can_add, _, max_keys = await tunnel_service.can_create_key(user.id)
                    await callback.message.edit_text(
                        f"📱 *Мои устройства* ({len(keys)}/{max_keys})\n\n"
                        f"Нажмите на устройство чтобы скопировать ключ.\n"
                        f"Нажмите ❌ чтобы удалить.",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=devices_keyboard(keys, can_add=can_add)
                    )
                else:
                    await callback_tunnel_menu(callback)
            else:
                await callback.answer("❌ Не удалось удалить ключ", show_alert=True)
    except Exception as e:
        logger.error(f"Error in callback_revoke_confirm: {e}")
        await callback.answer()


# === CALLBACK: ПЕРЕИМЕНОВАНИЕ УСТРОЙСТВА ===

@router.callback_query(F.data.startswith("tunnel:rename:"))
async def callback_rename(callback: types.CallbackQuery, state: FSMContext):
    """Запрос нового названия устройства"""
    try:
        key_id = int(callback.data.split(":")[2])

        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(callback.from_user.id)

            # SECURITY: Проверяем что ключ принадлежит пользователю
            result = await session.execute(
                select(TunnelKey).where(
                    TunnelKey.id == key_id,
                    TunnelKey.user_id == user.id
                )
            )
            key = result.scalar_one_or_none()

            if not key:
                await callback.answer("Устройство не найдено", show_alert=True)
                return

            await state.update_data(rename_key_id=key_id)
            await state.set_state(RenameStates.waiting_for_name)

            await callback.message.edit_text(
                f"✏️ *Переименование устройства*\n\n"
                f"Текущее название: *{key.device_name}*\n\n"
                f"Введите новое название:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_to_menu_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in callback_rename: {e}")
    await callback.answer()


@router.message(RenameStates.waiting_for_name)
async def process_rename(message: types.Message, state: FSMContext):
    """Обработка нового названия устройства"""
    try:
        data = await state.get_data()
        key_id = data.get("rename_key_id")

        if not key_id:
            await message.answer("❌ Ошибка. Попробуйте ещё раз.")
            await state.clear()
            return

        new_name = message.text.strip()[:50]  # Ограничение 50 символов

        if not new_name:
            await message.answer("❌ Название не может быть пустым. Попробуйте ещё раз.")
            return

        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(message.from_user.id)

            # Проверяем что ключ принадлежит пользователю
            result = await session.execute(
                select(TunnelKey).where(
                    TunnelKey.id == key_id,
                    TunnelKey.user_id == user.id
                )
            )
            key = result.scalar_one_or_none()

            if not key:
                await message.answer("❌ Устройство не найдено.")
                await state.clear()
                return

            # Обновляем название
            key.device_name = new_name
            await session.commit()

            await message.answer(
                f"✅ Устройство переименовано в *{new_name}*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_to_menu_keyboard()
            )

        await state.clear()
    except Exception as e:
        logger.error(f"Error in process_rename: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")
        await state.clear()


# === CALLBACK: ТАРИФЫ ===

@router.callback_query(F.data == "tunnel:plans")
async def callback_plans(callback: types.CallbackQuery):
    """Показать тарифы с текущим планом пользователя"""
    try:
        user_id = callback.from_user.id

        async with async_session() as session:
            limits_service = LimitsService(session)
            usage_info = await limits_service.get_usage_info(user_id)

        plan = usage_info["plan"]
        plan_name = usage_info["plan_name"]

        # Заголовок и текущий план
        text = f"💎 *Тарифные планы Джарвиса*\n\n"
        text += f"📊 *Ваш тариф: {plan_name}*\n"

        # Лимиты
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
                "• Статистика привычек\n"
                "_499₽ за 3 мес · 1699₽ за год_\n\n"
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
                "• Статистика + недельные отчёты\n"
                "_999₽ за 3 мес · 3399₽ за год_\n\n"
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
                "• Полная статистика + AI-советы\n"
                "_1499₽ за 3 мес · 4999₽ за год_\n"
            )

        if plan == "pro":
            text += "✨ У вас максимальный тариф!\n"

        text += "\n🎁 Есть промокод? Нажмите кнопку ниже"

        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=plans_keyboard(current_plan=plan, show_back=True)
        )
    except Exception as e:
        logger.error(f"Error in callback_plans: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("tunnel:buy:"))
async def callback_buy(callback: types.CallbackQuery):
    """Выбор периода подписки"""
    try:
        parts = callback.data.split(":")
        plan = parts[2]

        plan_names = {"basic": "Базовый", "standard": "Стандарт", "pro": "Про"}
        plan_name = plan_names.get(plan, plan)

        text = (
            f"📦 *{plan_name}*\n\n"
            f"Выберите период подписки:"
        )

        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=plan_periods_keyboard(plan)
        )
    except Exception as e:
        logger.error(f"Error in callback_buy: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("tunnel:pay:"))
async def callback_pay(callback: types.CallbackQuery):
    """Оплата (заглушка до подключения ЮKassa)"""
    try:
        parts = callback.data.split(":")
        plan = parts[2]
        months = parts[3]

        text = (
            f"💳 *Оплата*\n\n"
            f"План: *{plan.upper()}*\n"
            f"Период: *{months} мес.*\n\n"
            f"⚠️ Оплата временно недоступна.\n"
            f"Для активации подписки напишите @subbotin\\_core\n\n"
            f"Или используйте промокод, если он у вас есть."
        )

        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=promo_keyboard()
        )
    except Exception as e:
        logger.error(f"Error in callback_pay: {e}")
    await callback.answer()


# === ПРОМОКОД ===

@router.callback_query(F.data == "tunnel:promo")
async def callback_promo(callback: types.CallbackQuery, state: FSMContext):
    """Ввод промокода"""
    try:
        await callback.message.edit_text(
            "🎁 *Введите промокод:*\n\n"
            "Отправьте код в чат.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_to_menu_keyboard()
        )
        await state.set_state(PromoStates.waiting_for_code)
    except Exception as e:
        logger.error(f"Error in callback_promo: {e}")
    await callback.answer()


@router.message(PromoStates.waiting_for_code)
async def process_promo_code(message: types.Message, state: FSMContext):
    """Обработка промокода"""
    try:
        code = message.text.strip().upper()

        async with async_session() as session:
            memory = MemoryService(session)
            user, _ = await memory.get_or_create_user(message.from_user.id)

            # Ищем промокод в БД
            result = await session.execute(
                select(PromoCode).where(
                    PromoCode.code == code,
                    PromoCode.is_active == True
                )
            )
            promo = result.scalar_one_or_none()

            if not promo:
                await message.answer(
                    "❌ Промокод не найден или недействителен.\n\n"
                    "Попробуйте другой код или вернитесь в меню.",
                    reply_markup=back_to_menu_keyboard()
                )
                await state.clear()
                return

            # Проверяем лимит использований
            if promo.max_uses is not None and promo.current_uses >= promo.max_uses:
                await message.answer(
                    "❌ Лимит использований этого промокода исчерпан.",
                    reply_markup=back_to_menu_keyboard()
                )
                await state.clear()
                return

            # Проверяем, не использовал ли уже этот юзер этот промокод
            usage_check = await session.execute(
                select(PromoCodeUsage).where(
                    PromoCodeUsage.promo_code_id == promo.id,
                    PromoCodeUsage.user_id == user.id
                )
            )
            if usage_check.scalar_one_or_none():
                await message.answer(
                    "❌ Вы уже использовали этот промокод.",
                    reply_markup=back_to_menu_keyboard()
                )
                await state.clear()
                return

            # Вычисляем срок (days=0 означает бесконечно)
            if promo.days > 0:
                expires_at = datetime.utcnow() + timedelta(days=promo.days)
                expire_text = expires_at.strftime('%d.%m.%Y')
            else:
                expires_at = None  # Бесконечная подписка
                expire_text = "Бессрочно ♾️"

            # Создаём подписку
            subscription = Subscription(
                user_id=user.id,
                plan=promo.plan,
                status="active",
                started_at=datetime.utcnow(),
                expires_at=expires_at
            )
            session.add(subscription)
            await session.flush()  # Получаем ID подписки

            # Записываем использование промокода
            usage = PromoCodeUsage(
                promo_code_id=promo.id,
                user_id=user.id,
                subscription_id=subscription.id
            )
            session.add(usage)

            # Увеличиваем счётчик использований
            promo.current_uses += 1

            # ВАЖНО: Обновляем план в user для корректной работы LimitsService
            user.subscription_plan = promo.plan

            await session.commit()

            # Уведомляем админа об использовании промокода
            from services.admin_notify_service import get_admin_notify
            admin_notify = get_admin_notify()
            if admin_notify:
                await admin_notify.notify_promo_used(
                    telegram_id=message.from_user.id,
                    username=message.from_user.username,
                    promo_code=promo.code,
                    promo_type=promo.promo_type,
                    plan=promo.plan,
                    days=promo.days
                )

            # Сразу создаём VPN ключ для пользователя
            await message.answer(
                f"✅ *Промокод активирован!*\n\n"
                f"🎁 {promo.description}\n"
                f"📅 Действует до: {expire_text}\n\n"
                f"⏳ Создаю ваш ключ...",
                parse_mode=ParseMode.MARKDOWN
            )

            # Создаём ключ в Marzban (используем новую сессию чтобы гарантированно видеть подписку)
            async with async_session() as new_session:
                tunnel_service = TunnelService(new_session)
                sub_url, error = await tunnel_service.create_key(
                    user_id=user.id,
                    telegram_id=message.from_user.id,
                    full_name=message.from_user.full_name or "User",
                    device_name="Device"
                )

            logger.info(f"Promo key creation result for user {user.id}: sub_url={bool(sub_url)}, error={error}")

            if sub_url:
                await message.answer(
                    f"🔑 *Ваш ключ готов!*\n\n"
                    f"Скопируй ссылку и вставь в приложение Happ:\n\n"
                    f"`{sub_url}`\n\n"
                    f"_Нажми на ссылку, чтобы скопировать._\n\n"
                    f"📚 Нужна помощь? Нажмите «Инструкция» в меню туннеля.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_to_menu_keyboard()
                )
            else:
                logger.error(f"Failed to create key after promo for user {user.id}: {error}")
                await message.answer(
                    f"⚠️ Промокод активирован, но не удалось создать ключ.\n\n"
                    f"Перейдите в меню туннеля и нажмите «➕ Добавить устройство».",
                    reply_markup=back_to_menu_keyboard()
                )

        await state.clear()
    except Exception as e:
        logger.error(f"Error in process_promo_code: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")
        await state.clear()


# === CALLBACK: ПРОДЛИТЬ ===

@router.callback_query(F.data == "tunnel:renew")
async def callback_renew(callback: types.CallbackQuery):
    """Продление подписки"""
    await callback_plans(callback)


# === CALLBACK: ИНСТРУКЦИЯ ===

@router.callback_query(F.data == "tunnel:help")
async def callback_help(callback: types.CallbackQuery):
    """Инструкция по подключению"""
    try:
        text = (
            "📚 *Как подключить за 1 минуту*\n\n"
            "*1️⃣ Скопируй ключ*\n"
            "Нажми «🔑 Получить ключ» и скопируй ссылку.\n\n"
            "*2️⃣ Скачай приложение Happ:*\n\n"
            "🍏 *iPhone / iPad / Mac:*\n"
            "[Скачать в App Store](https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973)\n\n"
            "🤖 *Android:*\n"
            "[Скачать в Google Play](https://play.google.com/store/apps/details?id=com.happproxy)\n\n"
            "💻 *Windows:*\n"
            "[Скачать для Windows](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe)\n\n"
            "*3️⃣ Включи:*\n"
            "Вставь ключ и нажми кнопку подключения. Готово! 🚀"
        )

        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=back_to_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Error in callback_help: {e}")
    await callback.answer()
