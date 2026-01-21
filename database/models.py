"""
Модели базы данных (SQLAlchemy ORM).
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех моделей"""
    pass


class User(Base):
    """Пользователь бота"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Тарифный план
    subscription_plan: Mapped[str] = mapped_column(String(20), default="free")  # free/basic/standard/pro
    vpn_trial_used: Mapped[bool] = mapped_column(default=False)  # Использован ли VPN триал
    vpn_trial_expires: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Когда истекает триал

    # Трекинг напоминаний об истечении VPN
    vpn_reminder_3d_sent: Mapped[bool] = mapped_column(default=False)  # Отправлено ли напоминание за 3 дня
    vpn_reminder_1d_sent: Mapped[bool] = mapped_column(default=False)  # Отправлено ли напоминание за 1 день

    # Постоянная скидка (от промокода)
    permanent_discount_percent: Mapped[int] = mapped_column(Integer, default=0)  # Постоянная скидка %
    permanent_discount_promo_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Какой промокод дал скидку

    # Реферальная система
    referral_code: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True)  # Уникальный код для приглашения
    referred_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Кто пригласил (user.id)
    referral_bonus_days: Mapped[int] = mapped_column(Integer, default=0)  # Накопленные бонусные дни
    referral_count: Mapped[int] = mapped_column(Integer, default=0)  # Сколько людей пригласил (оплативших)

    # Google Calendar OAuth
    google_credentials: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # OAuth токены
    calendar_connected: Mapped[bool] = mapped_column(default=False)

    # Настройки
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow")
    morning_time: Mapped[str] = mapped_column(String(5), default="08:00")
    evening_time: Mapped[str] = mapped_column(String(5), default="21:00")
    focus_interval: Mapped[int] = mapped_column(Integer, default=120)  # минуты

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Отношения
    tasks: Mapped[list["Task"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    diary_entries: Mapped[list["DiaryEntry"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    habits: Mapped[list["Habit"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    memory_contexts: Mapped[list["MemoryContext"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    booking_links: Mapped[list["BookingLink"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    schedule_settings: Mapped[Optional["UserScheduleSettings"]] = relationship(back_populates="user", uselist=False)


class Task(Base):
    """Задачи пользователя"""
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1-5, где 1 = высший
    category: Mapped[str] = mapped_column(String(50), default="personal")  # work/personal/health/finance
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/in_progress/done
    is_quick_win: Mapped[bool] = mapped_column(default=False)  # "Быстрая победа"

    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="tasks")


class DiaryEntry(Base):
    """Записи дневника"""
    __tablename__ = "diary_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # ["work", "family", etc]
    entry_type: Mapped[str] = mapped_column(String(20), default="diary")  # diary/reflection/thought

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="diary_entries")


class Habit(Base):
    """Определение привычки пользователя"""
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    name: Mapped[str] = mapped_column(String(100))  # "Спорт", "Вода", "Медитация"
    emoji: Mapped[str] = mapped_column(String(10), default="✅")
    frequency: Mapped[str] = mapped_column(String(20), default="daily")  # daily/weekly
    target_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Целевое значение (8 стаканов, 30 мин)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # "стаканов", "минут", None для чекбокса
    is_active: Mapped[bool] = mapped_column(default=True)

    # Персональные напоминания
    reminder_times: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: ["07:00", "19:00"]
    reminder_days: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # "0,1,2,3,4,5,6" или "1,3,5"
    reminder_enabled: Mapped[bool] = mapped_column(default=True)

    # Смарт-напоминания (автоматически выученные)
    learned_times: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: {"0": ["09:15"], "1": ["09:30"]} по дням недели
    last_reminder_adjust: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Когда последний раз корректировали
    ignored_count: Mapped[int] = mapped_column(Integer, default=0)  # Сколько раз игнорировали напоминания подряд

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="habits")
    logs: Mapped[list["HabitLog"]] = relationship(back_populates="habit", cascade="all, delete-orphan")


class HabitLog(Base):
    """Лог выполнения привычки"""
    __tablename__ = "habit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    value: Mapped[int] = mapped_column(Integer, default=1)  # 1 = выполнено, или кол-во (стаканов воды)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)  # Дата выполнения

    habit: Mapped["Habit"] = relationship(back_populates="logs")


class UserStats(Base):
    """Статистика и геймификация пользователя"""
    __tablename__ = "user_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    xp: Mapped[int] = mapped_column(Integer, default=0)  # Очки опыта
    level: Mapped[int] = mapped_column(Integer, default=1)  # Уровень
    current_streak: Mapped[int] = mapped_column(Integer, default=0)  # Текущий стрик (дней подряд)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)  # Рекордный стрик
    last_activity_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Последняя активность
    achievements: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {"first_habit": true, ...}

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryContext(Base):
    """Долгосрочная память AI о пользователе"""
    __tablename__ = "memory_contexts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    key: Mapped[str] = mapped_column(String(100))  # goals/preferences/insights/facts
    value: Mapped[dict] = mapped_column(JSON)  # {"content": "...", "importance": 5}

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="memory_contexts")


class Conversation(Base):
    """История сообщений для контекста AI"""
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    role: Mapped[str] = mapped_column(String(20))  # user/assistant
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(20), default="text")  # text/voice/image

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="conversations")


class Reminder(Base):
    """Отложенные напоминания пользователя"""
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    message: Mapped[str] = mapped_column(Text)  # Текст напоминания
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Контекст (привычка, событие и т.д.)
    remind_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # Когда напомнить
    is_sent: Mapped[bool] = mapped_column(default=False)  # Отправлено ли

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApiUsageLog(Base):
    """Лог использования API (GPT, Whisper и др.)"""
    __tablename__ = "api_usage_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Тип запроса
    api_type: Mapped[str] = mapped_column(String(50))  # chat, voice, image, whisper, intent
    model: Mapped[str] = mapped_column(String(50))  # gpt-4o, whisper-1, etc

    # Токены
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Стоимость (приблизительная в центах)
    estimated_cost_cents: Mapped[float] = mapped_column(default=0.0)

    # Время выполнения в мс
    response_time_ms: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# === МОДЕЛИ ДЛЯ БРОНИРОВАНИЯ ВСТРЕЧ ===

class BookingLink(Base):
    """Ссылка для бронирования встреч"""
    __tablename__ = "booking_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Уникальный код для URL (abc123)
    slug: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    # Настройки
    title: Mapped[str] = mapped_column(String(200))  # "Консультация", "Звонок"
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)  # 15/30/60
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Статус
    is_active: Mapped[bool] = mapped_column(default=True)

    # Лимиты
    max_bookings_per_day: Mapped[int] = mapped_column(Integer, default=5)
    min_notice_hours: Mapped[int] = mapped_column(Integer, default=2)  # мин. за сколько часов до встречи
    max_days_ahead: Mapped[int] = mapped_column(Integer, default=14)  # макс. дней вперёд

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Отношения
    user: Mapped["User"] = relationship(back_populates="booking_links")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="booking_link", cascade="all, delete-orphan")


class UserScheduleSettings(Base):
    """Настройки рабочего расписания пользователя для бронирования"""
    __tablename__ = "user_schedule_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    # Рабочие часы (JSON: {"monday": {"start": "09:00", "end": "18:00"}, ...})
    # null = использовать morning_time/evening_time из User
    working_hours: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Буфер между встречами (минуты)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=15)

    # Дни недели, когда доступны встречи (0=пн, 6=вс)
    available_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")  # пн-пт по умолчанию

    # Отношения
    user: Mapped["User"] = relationship(back_populates="schedule_settings")


class Booking(Base):
    """Забронированная встреча"""
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_link_id: Mapped[int] = mapped_column(ForeignKey("booking_links.id"), index=True)

    # Данные гостя
    guest_name: Mapped[str] = mapped_column(String(200))
    guest_email: Mapped[str] = mapped_column(String(200))
    guest_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Тема/комментарий

    # Время встречи
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime)

    # Статус: confirmed, cancelled, completed
    status: Mapped[str] = mapped_column(String(20), default="confirmed")

    # ID события в Google Calendar (для отмены/редактирования)
    google_event_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Токен для отмены гостем
    cancel_token: Mapped[str] = mapped_column(String(50), unique=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Отношения
    booking_link: Mapped["BookingLink"] = relationship(back_populates="bookings")


# === МОДЕЛИ ДЛЯ VPN/ТУННЕЛЯ ===

class Subscription(Base):
    """Подписки пользователей"""
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    plan: Mapped[str] = mapped_column(String(20))  # 'free', 'basic', 'pro', 'premium'
    status: Mapped[str] = mapped_column(String(20), default="active")  # 'active', 'expired', 'cancelled'

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Автопродление
    auto_renew: Mapped[bool] = mapped_column(default=False)

    # Трекинг напоминаний об истечении
    reminder_3d_sent: Mapped[bool] = mapped_column(default=False)  # Напоминание за 3 дня
    reminder_1d_sent: Mapped[bool] = mapped_column(default=False)  # Напоминание за 1 день

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TunnelKey(Base):
    """Ключи VPN туннеля"""
    __tablename__ = "tunnel_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Email пользователя в Xray (jarvis_123_d1)
    xray_email: Mapped[str] = mapped_column(String(100), unique=True)

    # Название устройства
    device_name: Mapped[str] = mapped_column(String(50), default="Device")

    # Subscription URL для клиента
    subscription_url: Mapped[str] = mapped_column(Text)

    # Статус
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Payment(Base):
    """Платежи"""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    amount: Mapped[int] = mapped_column(Integer)  # В копейках
    currency: Mapped[str] = mapped_column(String(3), default="RUB")

    plan: Mapped[str] = mapped_column(String(20))  # Какой план оплачен
    months: Mapped[int] = mapped_column(Integer, default=1)  # На сколько месяцев

    provider: Mapped[str] = mapped_column(String(20))  # 'yookassa', 'stars', 'manual'
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending")  # 'pending', 'succeeded', 'failed', 'refunded'

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Referral(Base):
    """Реферальная программа"""
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True)

    referrer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # Кто пригласил
    referred_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # Кого пригласили

    status: Mapped[str] = mapped_column(String(20), default="registered")  # 'registered', 'paid', 'rewarded'
    reward_amount: Mapped[int] = mapped_column(Integer, default=0)  # Вознаграждение в копейках

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PromoCode(Base):
    """Гибкие промокоды"""
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(primary_key=True)

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # FRIEND90, WELCOME50
    description: Mapped[str] = mapped_column(String(200))  # "Скидка 50% на первый месяц"

    # === ТИП ПРОМОКОДА ===
    # subscription - даёт подписку на N дней (старое поведение)
    # discount_percent - скидка в % (разовая или постоянная)
    # discount_fixed - фиксированная скидка в рублях
    # trial_extend - продление триала
    promo_type: Mapped[str] = mapped_column(String(30), default="subscription")

    # === ДЛЯ subscription ===
    plan: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'basic', 'standard', 'pro'
    days: Mapped[int] = mapped_column(Integer, default=0)  # Кол-во дней подписки

    # === ДЛЯ discount_percent / discount_fixed ===
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)  # Скидка в % (10 = 10%)
    discount_amount: Mapped[int] = mapped_column(Integer, default=0)   # Фикс. скидка в копейках
    discount_permanent: Mapped[bool] = mapped_column(default=False)    # True = постоянная скидка

    # === ОГРАНИЧЕНИЯ ===
    # На какие планы распространяется (null = все)
    applies_to_plans: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # "basic,standard,pro"
    # Мин. период подписки для активации
    min_months: Mapped[int] = mapped_column(Integer, default=0)  # 0 = любой
    # Только для новых пользователей
    new_users_only: Mapped[bool] = mapped_column(default=False)

    # === ЛИМИТЫ ===
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # null = безлимит
    current_uses: Mapped[int] = mapped_column(Integer, default=0)
    max_uses_per_user: Mapped[int] = mapped_column(Integer, default=1)  # Сколько раз 1 юзер может использовать

    # === СРОК ДЕЙСТВИЯ ===
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # null = бессрочный

    # Статус
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Отношения
    usages: Mapped[list["PromoCodeUsage"]] = relationship(back_populates="promo_code", cascade="all, delete-orphan")


class PromoCodeUsage(Base):
    """Использование промокодов"""
    __tablename__ = "promo_code_usages"

    id: Mapped[int] = mapped_column(primary_key=True)

    promo_code_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Какая подписка была создана
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)

    # Детали применения
    discount_applied: Mapped[int] = mapped_column(Integer, default=0)  # Сумма скидки в копейках
    original_price: Mapped[int] = mapped_column(Integer, default=0)    # Исходная цена
    final_price: Mapped[int] = mapped_column(Integer, default=0)       # Итоговая цена

    used_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Отношения
    promo_code: Mapped["PromoCode"] = relationship(back_populates="usages")


class DailyUsage(Base):
    """Дневные счётчики использования для лимитов"""
    __tablename__ = "daily_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Дата (только дата, без времени)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)

    # Счётчики
    ai_requests: Mapped[int] = mapped_column(Integer, default=0)  # AI запросы
    reminders_created: Mapped[int] = mapped_column(Integer, default=0)  # Созданные напоминания
    calendar_reminders: Mapped[int] = mapped_column(Integer, default=0)  # Напоминания календаря

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
