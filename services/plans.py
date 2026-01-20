"""
Конфигурация тарифных планов Джарвиса.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PlanLimits:
    """Лимиты тарифного плана"""
    # Привычки
    habits_max: int  # Макс. кол-во привычек (0 = безлимит)

    # Напоминания
    reminders_per_day: int  # Макс. напоминаний в день (0 = безлимит)

    # Календарь
    calendar_reminders_per_day: int  # Макс. напоминаний календаря в день (0 = безлимит)

    # AI
    ai_requests_per_day: int  # Макс. AI запросов в день (0 = безлимит)

    # VPN
    vpn_devices: int  # Кол-во устройств (0 = нет доступа)
    vpn_trial_days: int  # Пробный период VPN (0 = нет)

    # Букинг
    booking_links_max: int  # Макс. ссылок для бронирования (0 = нет доступа)

    # Аналитика
    analytics_enabled: bool  # Базовая аналитика
    analytics_weekly: bool  # Недельные отчёты
    analytics_ai_insights: bool  # AI-инсайты


# Конфигурация тарифов
PLANS = {
    "free": PlanLimits(
        habits_max=3,
        reminders_per_day=3,
        calendar_reminders_per_day=3,
        ai_requests_per_day=5,
        vpn_devices=0,
        vpn_trial_days=7,  # 7 дней триал
        booking_links_max=0,
        analytics_enabled=False,
        analytics_weekly=False,
        analytics_ai_insights=False,
    ),
    "basic": PlanLimits(
        habits_max=5,
        reminders_per_day=10,
        calendar_reminders_per_day=10,
        ai_requests_per_day=50,
        vpn_devices=1,
        vpn_trial_days=0,
        booking_links_max=1,
        analytics_enabled=True,
        analytics_weekly=False,
        analytics_ai_insights=False,
    ),
    "standard": PlanLimits(
        habits_max=10,
        reminders_per_day=20,
        calendar_reminders_per_day=20,
        ai_requests_per_day=100,
        vpn_devices=3,
        vpn_trial_days=0,
        booking_links_max=5,
        analytics_enabled=True,
        analytics_weekly=True,
        analytics_ai_insights=False,
    ),
    "pro": PlanLimits(
        habits_max=0,  # Безлимит
        reminders_per_day=0,  # Безлимит
        calendar_reminders_per_day=0,  # Безлимит
        ai_requests_per_day=0,  # Безлимит
        vpn_devices=5,
        vpn_trial_days=0,
        booking_links_max=0,  # Безлимит
        analytics_enabled=True,
        analytics_weekly=True,
        analytics_ai_insights=True,
    ),
}

# Цены (в копейках)
PLAN_PRICES = {
    "basic": {"1": 19900, "3": 49900, "12": 179900},      # 199₽/мес
    "standard": {"1": 39900, "3": 99900, "12": 359900},   # 399₽/мес
    "pro": {"1": 79900, "3": 199900, "12": 719900},       # 799₽/мес
}

# Названия планов для отображения
PLAN_NAMES = {
    "free": "Бесплатный",
    "basic": "Базовый",
    "standard": "Стандарт",
    "pro": "Про",
}


def get_plan_limits(plan: str) -> PlanLimits:
    """Получить лимиты плана"""
    return PLANS.get(plan, PLANS["free"])


def get_plan_price(plan: str, months: int = 1) -> int:
    """Получить цену плана в копейках"""
    if plan not in PLAN_PRICES:
        return 0
    return PLAN_PRICES[plan].get(str(months), PLAN_PRICES[plan]["1"])


def get_plan_name(plan: str) -> str:
    """Получить название плана"""
    return PLAN_NAMES.get(plan, plan.capitalize())


def is_limit_exceeded(current: int, limit: int) -> bool:
    """Проверить превышен ли лимит (0 = безлимит)"""
    if limit == 0:
        return False
    return current >= limit
