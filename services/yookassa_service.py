"""
Сервис для работы с ЮKassa.
Создание платежей, обработка вебхуков, активация подписок.
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotificationEventType, WebhookNotificationFactory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import User, Payment as PaymentModel, Subscription

logger = logging.getLogger(__name__)

# Конфигурация ЮKassa
if config.YOOKASSA_SHOP_ID and config.YOOKASSA_SECRET_KEY:
    Configuration.account_id = config.YOOKASSA_SHOP_ID
    Configuration.secret_key = config.YOOKASSA_SECRET_KEY


# Цены на тарифы (в рублях)
PLAN_PRICES = {
    "basic": {
        1: 199,    # 1 месяц
        3: 499,    # 3 месяца (скидка ~17%)
        12: 1499,  # 12 месяцев (скидка ~37%)
    },
    "standard": {
        1: 399,
        3: 999,    # ~17% скидка
        12: 2999,  # ~37% скидка
    },
    "pro": {
        1: 599,
        3: 1499,   # ~17% скидка
        12: 4499,  # ~37% скидка
    },
}

PLAN_NAMES = {
    "basic": "Базовый",
    "standard": "Стандарт",
    "pro": "Про",
}


class YookassaService:
    """Сервис для работы с платежами ЮKassa"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_payment(
        self,
        user_id: int,
        telegram_id: int,
        plan: str,
        months: int = 1,
        promo_discount: int = 0,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Создать платёж в ЮKassa.

        Args:
            user_id: ID пользователя в БД
            telegram_id: Telegram ID для возврата
            plan: Тарифный план (basic, standard, pro)
            months: Количество месяцев (1, 3, 12)
            promo_discount: Скидка по промокоду (в рублях)

        Returns:
            (payment_url, payment_id) или (None, None) при ошибке
        """
        if not config.YOOKASSA_SHOP_ID or not config.YOOKASSA_SECRET_KEY:
            logger.error("ЮKassa не настроена")
            return None, None

        if plan not in PLAN_PRICES:
            logger.error(f"Неизвестный план: {plan}")
            return None, None

        if months not in PLAN_PRICES[plan]:
            logger.error(f"Неизвестный период: {months} месяцев")
            return None, None

        # Рассчитываем сумму
        amount = PLAN_PRICES[plan][months] - promo_discount
        if amount < 1:
            amount = 1  # Минимум 1 рубль

        # Создаём запись в БД
        payment_record = PaymentModel(
            user_id=user_id,
            amount=amount * 100,  # В копейках
            currency="RUB",
            plan=plan,
            months=months,
            provider="yookassa",
            status="pending",
        )
        self.session.add(payment_record)
        await self.session.commit()
        await self.session.refresh(payment_record)

        # Описание платежа
        plan_name = PLAN_NAMES.get(plan, plan)
        months_word = "месяц" if months == 1 else "месяца" if months in [2, 3, 4] else "месяцев"
        description = f"Джарвис {plan_name} — {months} {months_word}"

        try:
            # Создаём платёж в ЮKassa
            idempotence_key = str(uuid.uuid4())
            payment = Payment.create({
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": config.YOOKASSA_RETURN_URL
                },
                "capture": True,  # Автоматическое списание
                "description": description,
                "receipt": {
                    "customer": {
                        "email": "customer@example.com"  # Можно заменить на реальный email если есть
                    },
                    "items": [
                        {
                            "description": description,
                            "quantity": "1.00",
                            "amount": {
                                "value": f"{amount:.2f}",
                                "currency": "RUB"
                            },
                            "vat_code": 1,  # Без НДС (для ИП на УСН)
                            "payment_mode": "full_payment",
                            "payment_subject": "service"
                        }
                    ]
                },
                "metadata": {
                    "user_id": user_id,
                    "telegram_id": telegram_id,
                    "plan": plan,
                    "months": months,
                    "payment_id": payment_record.id,
                }
            }, idempotence_key)

            # Сохраняем ID платежа ЮKassa
            payment_record.provider_payment_id = payment.id
            await self.session.commit()

            logger.info(f"Создан платёж {payment.id} для user_id={user_id}, plan={plan}, months={months}")

            return payment.confirmation.confirmation_url, payment.id

        except Exception as e:
            logger.error(f"Ошибка создания платежа: {e}")
            payment_record.status = "failed"
            await self.session.commit()
            return None, None

    async def process_webhook(self, body: dict) -> bool:
        """
        Обработать вебхук от ЮKassa.

        Args:
            body: Тело запроса от ЮKassa

        Returns:
            True если обработано успешно
        """
        try:
            notification = WebhookNotificationFactory().create(body)
            payment_data = notification.object

            logger.info(f"Получен вебхук: {notification.event}, payment_id={payment_data.id}")

            if notification.event == WebhookNotificationEventType.PAYMENT_SUCCEEDED:
                return await self._handle_payment_succeeded(payment_data)

            elif notification.event == WebhookNotificationEventType.PAYMENT_CANCELED:
                return await self._handle_payment_canceled(payment_data)

            elif notification.event == WebhookNotificationEventType.REFUND_SUCCEEDED:
                return await self._handle_refund_succeeded(payment_data)

            return True

        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {e}")
            return False

    async def _handle_payment_succeeded(self, payment_data) -> bool:
        """Обработка успешного платежа"""
        try:
            metadata = payment_data.metadata or {}
            user_id = metadata.get("user_id")
            plan = metadata.get("plan")
            months = int(metadata.get("months", 1))
            local_payment_id = metadata.get("payment_id")

            if not user_id or not plan:
                logger.error(f"Неполные metadata в платеже {payment_data.id}")
                return False

            # Обновляем статус платежа в БД
            if local_payment_id:
                result = await self.session.execute(
                    select(PaymentModel).where(PaymentModel.id == int(local_payment_id))
                )
                payment_record = result.scalar_one_or_none()
                if payment_record:
                    payment_record.status = "succeeded"
                    payment_record.paid_at = datetime.utcnow()

            # Активируем подписку
            await self._activate_subscription(int(user_id), plan, months)

            await self.session.commit()
            logger.info(f"Платёж {payment_data.id} обработан, подписка активирована")
            return True

        except Exception as e:
            logger.error(f"Ошибка обработки успешного платежа: {e}")
            return False

    async def _handle_payment_canceled(self, payment_data) -> bool:
        """Обработка отменённого платежа"""
        try:
            metadata = payment_data.metadata or {}
            local_payment_id = metadata.get("payment_id")

            if local_payment_id:
                result = await self.session.execute(
                    select(PaymentModel).where(PaymentModel.id == int(local_payment_id))
                )
                payment_record = result.scalar_one_or_none()
                if payment_record:
                    payment_record.status = "failed"
                    await self.session.commit()

            logger.info(f"Платёж {payment_data.id} отменён")
            return True

        except Exception as e:
            logger.error(f"Ошибка обработки отменённого платежа: {e}")
            return False

    async def _handle_refund_succeeded(self, refund_data) -> bool:
        """Обработка успешного возврата"""
        try:
            # Находим платёж по ID из ЮKassa
            payment_id = refund_data.payment_id

            result = await self.session.execute(
                select(PaymentModel).where(PaymentModel.provider_payment_id == payment_id)
            )
            payment_record = result.scalar_one_or_none()

            if payment_record:
                payment_record.status = "refunded"
                await self.session.commit()

            logger.info(f"Возврат по платежу {payment_id} обработан")
            return True

        except Exception as e:
            logger.error(f"Ошибка обработки возврата: {e}")
            return False

    async def _activate_subscription(self, user_id: int, plan: str, months: int):
        """Активировать или продлить подписку пользователя"""
        # Получаем пользователя
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            logger.error(f"Пользователь {user_id} не найден")
            return

        # Ищем активную подписку
        result = await self.session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == "active"
            )
        )
        existing_sub = result.scalar_one_or_none()

        now = datetime.utcnow()
        duration = timedelta(days=30 * months)

        if existing_sub:
            # Продлеваем существующую
            if existing_sub.expires_at and existing_sub.expires_at > now:
                # Если ещё активна — добавляем к текущей дате окончания
                existing_sub.expires_at = existing_sub.expires_at + duration
            else:
                # Если истекла — от текущей даты
                existing_sub.expires_at = now + duration

            # Обновляем план если выше
            plan_order = ["free", "basic", "standard", "pro"]
            if plan_order.index(plan) > plan_order.index(existing_sub.plan):
                existing_sub.plan = plan

            # Сбрасываем флаги напоминаний
            existing_sub.reminder_3d_sent = False
            existing_sub.reminder_1d_sent = False
        else:
            # Создаём новую подписку
            new_sub = Subscription(
                user_id=user_id,
                plan=plan,
                status="active",
                started_at=now,
                expires_at=now + duration,
            )
            self.session.add(new_sub)

        # Обновляем VPN триал (если был)
        if user.vpn_trial_used and user.vpn_trial_expires:
            # Сбрасываем триал — теперь платная подписка
            user.vpn_reminder_3d_sent = False
            user.vpn_reminder_1d_sent = False

        logger.info(f"Подписка {plan} активирована для user_id={user_id} на {months} мес.")


def get_plan_price(plan: str, months: int) -> Optional[int]:
    """Получить цену тарифа"""
    if plan in PLAN_PRICES and months in PLAN_PRICES[plan]:
        return PLAN_PRICES[plan][months]
    return None


def get_plan_prices() -> dict:
    """Получить все цены"""
    return PLAN_PRICES
