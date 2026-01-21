"""
Сервис уведомлений администратора.
Отправляет сообщения владельцу при важных событиях.
"""
import logging
from aiogram import Bot
from config import config

logger = logging.getLogger(__name__)


class AdminNotifyService:
    """Отправка уведомлений администратору"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.owner_id = config.OWNER_TELEGRAM_ID

    async def notify(self, message: str):
        """Отправить уведомление владельцу"""
        if not self.owner_id:
            logger.warning("OWNER_TELEGRAM_ID not set, skipping notification")
            return

        try:
            await self.bot.send_message(
                self.owner_id,
                message,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")

    async def notify_new_user(self, telegram_id: int, username: str = None, first_name: str = None, referral_code: str = None):
        """Уведомление о новом пользователе"""
        name = username or first_name or str(telegram_id)
        display_name = f"@{username}" if username else (first_name or f"ID: {telegram_id}")

        ref_text = f"\n📎 Реферал: <code>{referral_code}</code>" if referral_code else ""

        await self.notify(
            f"👤 <b>Новый пользователь</b>\n\n"
            f"Имя: {display_name}\n"
            f"ID: <code>{telegram_id}</code>{ref_text}"
        )

    async def notify_promo_used(self, telegram_id: int, username: str, promo_code: str, promo_type: str, plan: str = None, days: int = None):
        """Уведомление об использовании промокода"""
        display_name = f"@{username}" if username else f"ID: {telegram_id}"

        value_text = ""
        if promo_type == "subscription" and plan:
            value_text = f"\n📦 План: {plan.upper()}, {days} дней"
        elif promo_type == "trial_extend" and days:
            value_text = f"\n📦 Триал +{days} дней"

        await self.notify(
            f"🎁 <b>Промокод активирован</b>\n\n"
            f"Пользователь: {display_name}\n"
            f"ID: <code>{telegram_id}</code>\n"
            f"Промокод: <code>{promo_code}</code>{value_text}"
        )

    async def notify_subscription(self, telegram_id: int, username: str, plan: str, months: int, amount: int):
        """Уведомление о новой подписке"""
        display_name = f"@{username}" if username else f"ID: {telegram_id}"
        amount_rub = amount / 100  # копейки в рубли

        await self.notify(
            f"💳 <b>Новая подписка</b>\n\n"
            f"Пользователь: {display_name}\n"
            f"ID: <code>{telegram_id}</code>\n"
            f"План: {plan.upper()}\n"
            f"Период: {months} мес.\n"
            f"Сумма: {amount_rub:.0f}₽"
        )

    async def notify_vpn_key_created(self, telegram_id: int, username: str, device_name: str):
        """Уведомление о создании VPN ключа"""
        display_name = f"@{username}" if username else f"ID: {telegram_id}"

        await self.notify(
            f"🔐 <b>VPN ключ создан</b>\n\n"
            f"Пользователь: {display_name}\n"
            f"ID: <code>{telegram_id}</code>\n"
            f"Устройство: {device_name}"
        )


# Глобальный экземпляр (инициализируется при старте бота)
admin_notify: AdminNotifyService = None


def init_admin_notify(bot: Bot):
    """Инициализировать сервис уведомлений"""
    global admin_notify
    admin_notify = AdminNotifyService(bot)
    return admin_notify


def get_admin_notify() -> AdminNotifyService:
    """Получить сервис уведомлений"""
    return admin_notify
