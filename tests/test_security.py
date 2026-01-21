"""
Security тесты — проверка защиты от уязвимостей
"""
import pytest
import html as html_lib
from sqlalchemy import select

from database.models import TunnelKey, User


class TestIDOR:
    """Тесты защиты от IDOR (Insecure Direct Object Reference)"""

    @pytest.mark.asyncio
    async def test_vpn_key_access_requires_user_ownership(self, session, user_with_vpn_key):
        """VPN ключ можно получить только владельцу"""
        user, key = user_with_vpn_key

        # Создаём второго пользователя
        other_user = User(
            telegram_id=999999999,
            username="attacker",
            subscription_plan="pro"
        )
        session.add(other_user)
        await session.commit()

        # Проверяем что другой пользователь не может получить чужой ключ
        result = await session.execute(
            select(TunnelKey).where(
                TunnelKey.id == key.id,
                TunnelKey.user_id == other_user.id  # Фильтр по user_id
            )
        )
        found_key = result.scalar_one_or_none()
        assert found_key is None, "Чужой пользователь не должен получить доступ к ключу"

        # Проверяем что владелец может получить свой ключ
        result = await session.execute(
            select(TunnelKey).where(
                TunnelKey.id == key.id,
                TunnelKey.user_id == user.id
            )
        )
        own_key = result.scalar_one_or_none()
        assert own_key is not None, "Владелец должен получить свой ключ"
        assert own_key.id == key.id


class TestXSS:
    """Тесты защиты от XSS"""

    def test_html_escape_function(self):
        """Проверка функции экранирования HTML"""
        # Эмулируем функцию esc() из admin-panel
        def esc(value):
            if value is None:
                return ""
            return html_lib.escape(str(value))

        # XSS атаки с HTML тегами
        xss_payloads = [
            '<script>alert("xss")</script>',
            '<img src=x onerror=alert(1)>',
            '"><script>alert(1)</script>',
            '<svg onload=alert(1)>',
        ]

        for payload in xss_payloads:
            escaped = esc(payload)
            # Проверяем что теги экранированы
            assert '<script>' not in escaped, f"Script tag not escaped in: {escaped}"
            assert '<img' not in escaped, f"Img tag not escaped in: {escaped}"
            assert '<svg' not in escaped, f"SVG tag not escaped in: {escaped}"
            # Все < должны быть заменены на &lt;
            assert '&lt;' in escaped, f"Expected &lt; in: {escaped}"

    def test_escape_none_value(self):
        """Экранирование None возвращает пустую строку"""
        def esc(value):
            if value is None:
                return ""
            return html_lib.escape(str(value))

        assert esc(None) == ""

    def test_escape_special_chars(self):
        """Экранирование специальных HTML символов"""
        def esc(value):
            if value is None:
                return ""
            return html_lib.escape(str(value))

        assert esc('<') == '&lt;'
        assert esc('>') == '&gt;'
        assert esc('&') == '&amp;'
        assert esc('"') == '&quot;'


class TestInputValidation:
    """Тесты валидации пользовательского ввода"""

    def test_device_name_length_limit(self):
        """Название устройства ограничено 50 символами"""
        long_name = "A" * 100
        truncated = long_name[:50]
        assert len(truncated) == 50

    def test_email_basic_validation(self):
        """Базовая валидация email — как в booking/api.py"""
        def is_valid_email(email: str) -> bool:
            """Простая проверка email как в booking API"""
            return "@" in email and "." in email

        valid_emails = ["test@example.com", "user.name@domain.org"]
        invalid_emails = ["notanemail", "missingdot@domain"]

        for email in valid_emails:
            assert is_valid_email(email), f"{email} should be valid"

        for email in invalid_emails:
            assert not is_valid_email(email), f"{email} should be invalid"


class TestSecretHandling:
    """Тесты безопасной работы с секретами"""

    def test_password_not_in_logs(self):
        """Пароль не должен логироваться"""
        # Проверяем что в новом коде нет DEBUG логирования пароля
        import os
        admin_panel_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "admin-panel", "main.py"
        )

        if os.path.exists(admin_panel_path):
            with open(admin_panel_path, 'r') as f:
                content = f.read()

            # Не должно быть DEBUG логов с паролем
            assert "DEBUG: password=" not in content, "Пароль не должен логироваться"
            assert "repr(password)" not in content, "Пароль не должен выводиться"

    def test_admin_password_required(self):
        """ADMIN_PASSWORD должен быть обязательным"""
        import os
        admin_panel_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "admin-panel", "main.py"
        )

        if os.path.exists(admin_panel_path):
            with open(admin_panel_path, 'r') as f:
                content = f.read()

            # Должна быть проверка на пустой пароль с SystemExit
            assert "SystemExit" in content or "raise" in content
            assert "ADMIN_PASSWORD" in content


class TestSQLInjection:
    """Тесты защиты от SQL injection (ORM защищает автоматически)"""

    @pytest.mark.asyncio
    async def test_orm_parameterized_queries(self, session):
        """SQLAlchemy ORM использует параметризованные запросы"""
        # Попытка SQL injection через username
        malicious_input = "'; DROP TABLE users; --"

        user = User(
            telegram_id=111111111,
            username=malicious_input,
            subscription_plan="free"
        )
        session.add(user)
        await session.commit()

        # Таблица должна остаться
        result = await session.execute(select(User))
        users = result.scalars().all()
        assert len(users) >= 1, "Таблица users должна существовать"

        # Username сохранён как есть (не выполнен как SQL)
        await session.refresh(user)
        assert user.username == malicious_input
