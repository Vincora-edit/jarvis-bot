"""
Admin Panel для Jarvis Bot
"""
import os
import html as html_lib
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

import aiosqlite

load_dotenv()


def esc(value) -> str:
    """SECURITY: Экранирование HTML для защиты от XSS"""
    if value is None:
        return ""
    return html_lib.escape(str(value))

# VPN конфигурация (legacy Marzban удалён, используем Xray напрямую)

# Конфигурация
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", "")

# SECURITY: Проверяем что секреты заданы
if not ADMIN_SESSION_SECRET:
    import secrets
    ADMIN_SESSION_SECRET = secrets.token_hex(32)
    print("WARNING: ADMIN_SESSION_SECRET not set in .env, using random value")

if not ADMIN_PASSWORD:
    print("WARNING: ADMIN_PASSWORD not set in .env!")

app = FastAPI(title="Jarvis Admin Panel")
app.add_middleware(
    SessionMiddleware,
    secret_key=ADMIN_SESSION_SECRET,
    session_cookie="admin_session",
    max_age=86400,  # 1 день (было 7 — слишком долго)
)
JARVIS_DB_PATH = os.getenv("JARVIS_DB_PATH", "/opt/jarvis-bot/bot_database.db")


# === AUTH ===

# SECURITY: Rate limiting для защиты от bruteforce
import time
from collections import defaultdict

_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_RATE_LIMIT = 5  # максимум попыток
_LOGIN_RATE_WINDOW = 300  # за 5 минут (секунд)


def _check_rate_limit(ip: str) -> bool:
    """Проверить rate limit для IP. Возвращает True если превышен лимит."""
    now = time.time()
    # Удаляем старые попытки
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _LOGIN_RATE_WINDOW]
    return len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT


def _record_login_attempt(ip: str):
    """Записать попытку логина"""
    _login_attempts[ip].append(time.time())


def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None, blocked: str = None):
    html = LOGIN_HTML
    if blocked:
        html = html.replace("<!-- ERROR -->", '<p style="color: #dc3545; text-align: center; margin-bottom: 15px;">Слишком много попыток. Подождите 5 минут.</p>')
    elif error:
        html = html.replace("<!-- ERROR -->", '<p style="color: #dc3545; text-align: center; margin-bottom: 15px;">Неверный пароль</p>')
    return HTMLResponse(html)


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"

    # SECURITY: Проверяем rate limit
    if _check_rate_limit(client_ip):
        return RedirectResponse(url="/login?blocked=1", status_code=303)

    _record_login_attempt(client_ip)

    # DEBUG: Логируем попытку входа
    print(f"LOGIN ATTEMPT: ip={client_ip}, password_len={len(password)}, stored_len={len(ADMIN_PASSWORD) if ADMIN_PASSWORD else 0}")
    print(f"DEBUG: password={repr(password)}, stored={repr(ADMIN_PASSWORD)}")

    # SECURITY: Сравнение через hmac для защиты от timing attack
    import hmac
    if ADMIN_PASSWORD and hmac.compare_digest(password, ADMIN_PASSWORD):
        request.session["user"] = "admin"
        # Сбрасываем счётчик при успешном входе
        _login_attempts[client_ip] = []
        print(f"LOGIN SUCCESS: ip={client_ip}")
        return RedirectResponse(url="/", status_code=303)

    print(f"LOGIN FAILED: ip={client_ip}")
    return RedirectResponse(url="/login?error=1", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# === DASHBOARD (Jarvis) ===

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    data = await get_jarvis_stats()
    html = render_jarvis_dashboard(data)
    return HTMLResponse(html)


# === РЕФЕРАЛЫ ===

@app.get("/referrals", response_class=HTMLResponse)
async def referrals_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    data = await get_referral_stats()
    html = render_referrals_page(data)
    return HTMLResponse(html)


async def get_referral_stats():
    """Статистика реферальной программы"""
    data = {"top_referrers": [], "recent_referrals": [], "summary": {}, "error": None}

    try:
        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Общая статистика
            cursor = await db.execute("""
                SELECT
                    COUNT(DISTINCT referred_by_user_id) as referrers_count,
                    COUNT(*) as total_referrals,
                    SUM(CASE WHEN referred_by_user_id IS NOT NULL THEN 1 ELSE 0 END) as invited_users
                FROM users WHERE referred_by_user_id IS NOT NULL
            """)
            row = await cursor.fetchone()
            data["summary"] = {
                "referrers_count": row[0] or 0,
                "total_referrals": row[1] or 0,
            }

            # Топ рефереров
            cursor = await db.execute("""
                SELECT
                    u.id, u.telegram_id, u.username, u.first_name,
                    u.referral_code, u.referral_count, u.referral_bonus_days
                FROM users u
                WHERE u.referral_count > 0
                ORDER BY u.referral_count DESC
                LIMIT 20
            """)
            rows = await cursor.fetchall()
            for row in rows:
                data["top_referrers"].append({
                    "id": row[0],
                    "telegram_id": row[1],
                    "username": row[2] or row[3] or f"ID:{row[1]}",
                    "referral_code": row[4],
                    "referral_count": row[5],
                    "bonus_days": row[6],
                })

            # Последние рефералы
            cursor = await db.execute("""
                SELECT
                    u.username, u.first_name, u.telegram_id, u.created_at,
                    r.username as referrer_username, r.first_name as referrer_name
                FROM users u
                JOIN users r ON u.referred_by_user_id = r.id
                ORDER BY u.created_at DESC
                LIMIT 20
            """)
            rows = await cursor.fetchall()
            for row in rows:
                data["recent_referrals"].append({
                    "username": row[0] or row[1] or f"ID:{row[2]}",
                    "created_at": row[3][:16].replace("T", " ") if row[3] else "-",
                    "referrer": row[4] or row[5] or "Unknown",
                })

    except Exception as e:
        data["error"] = str(e)

    return data


def render_referrals_page(data: dict) -> str:
    """Рендер страницы рефералов"""
    error = data.get("error")
    summary = data.get("summary", {})

    # Топ рефереры
    top_rows = ""
    for i, r in enumerate(data.get("top_referrers", []), 1):
        top_rows += f"""
        <tr>
            <td>{i}</td>
            <td>{esc(r['username'])}</td>
            <td><code>{esc(r['referral_code']) or '-'}</code></td>
            <td>{r['referral_count']}</td>
            <td>{r['bonus_days']} дн.</td>
        </tr>
        """

    # Последние рефералы
    recent_rows = ""
    for r in data.get("recent_referrals", []):
        recent_rows += f"""
        <tr>
            <td>{esc(r['created_at'])}</td>
            <td>{esc(r['username'])}</td>
            <td>{esc(r['referrer'])}</td>
        </tr>
        """

    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Рефералы — Admin</title>
    {COMMON_STYLES}
</head>
<body>
    <div class="container">
        <header>
            <h1>👥 Рефералы</h1>
            <nav>
                <a href="/">Dashboard</a>
                <a href="/promo">Промокоды</a>
                <a href="/referrals" class="active">Рефералы</a>
                <a href="/logout">Выйти</a>
            </nav>
        </header>

        {"<div class='error'>Ошибка: " + str(error) + "</div>" if error else ""}

        <div class="stats-row">
            <div class="stat-card green">
                <div class="stat-value">{summary.get('referrers_count', 0)}</div>
                <div class="stat-label">Активных рефереров</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">{summary.get('total_referrals', 0)}</div>
                <div class="stat-label">Всего приглашённых</div>
            </div>
        </div>

        <div class="grid-2">
            <div class="section">
                <h2>Топ рефереров</h2>
                <table>
                    <thead>
                        <tr><th>#</th><th>Пользователь</th><th>Код</th><th>Оплативших</th><th>Бонус</th></tr>
                    </thead>
                    <tbody>
                        {top_rows if top_rows else "<tr><td colspan='5' class='empty'>Нет данных</td></tr>"}
                    </tbody>
                </table>
            </div>

            <div class="section">
                <h2>Последние рефералы</h2>
                <table>
                    <thead>
                        <tr><th>Дата</th><th>Пользователь</th><th>Пригласил</th></tr>
                    </thead>
                    <tbody>
                        {recent_rows if recent_rows else "<tr><td colspan='3' class='empty'>Нет данных</td></tr>"}
                    </tbody>
                </table>
            </div>
        </div>

        <p class="footer">Обновлено: {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
    </div>
</body>
</html>
    """


# === ПРОМОКОДЫ ===

@app.get("/promo", response_class=HTMLResponse)
async def promo_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    data = await get_promo_stats()
    html = render_promo_page(data)
    return HTMLResponse(html)


@app.post("/promo/create")
async def create_promo(
    request: Request,
    code: str = Form(...),
    promo_type: str = Form(...),
    description: str = Form(...),
    # Для subscription
    plan: str = Form(None),
    days: int = Form(0),
    # Для discount
    discount_percent: int = Form(0),
    discount_amount: int = Form(0),
    discount_permanent: bool = Form(False),
    # Ограничения
    new_users_only: bool = Form(False),
    # Лимиты
    max_uses: int = Form(None),
    max_uses_per_user: int = Form(1),
    expires_days: int = Form(0)  # 0 = бессрочный
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    try:
        # Расчёт даты истечения
        expires_at = None
        if expires_days and expires_days > 0:
            from datetime import timedelta
            expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()

        # Скидка в копейках
        discount_amount_kopecks = discount_amount * 100 if discount_amount else 0

        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            await db.execute("""
                INSERT INTO promo_codes (
                    code, promo_type, description,
                    plan, days,
                    discount_percent, discount_amount, discount_permanent,
                    applies_to_plans, min_months, new_users_only,
                    max_uses, max_uses_per_user, expires_at,
                    is_active, current_uses, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, datetime('now'))
            """, (
                code.upper(),
                promo_type,
                description,
                plan if plan else None,
                days,
                discount_percent,
                discount_amount_kopecks,
                1 if discount_permanent else 0,
                None,  # applies_to_plans
                0,     # min_months
                1 if new_users_only else 0,
                max_uses if max_uses and max_uses > 0 else None,
                max_uses_per_user,
                expires_at
            ))
            await db.commit()
    except Exception as e:
        print(f"Error creating promo: {e}")  # Для отладки

    return RedirectResponse(url="/promo", status_code=302)


@app.get("/promo/toggle/{promo_id}")
async def toggle_promo(request: Request, promo_id: int):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    async with aiosqlite.connect(JARVIS_DB_PATH) as db:
        await db.execute("""
            UPDATE promo_codes SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
            WHERE id = ?
        """, (promo_id,))
        await db.commit()

    return RedirectResponse(url="/promo", status_code=302)


@app.get("/promo/delete/{promo_id}")
async def delete_promo(request: Request, promo_id: int):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    async with aiosqlite.connect(JARVIS_DB_PATH) as db:
        await db.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
        await db.commit()

    return RedirectResponse(url="/promo", status_code=302)


@app.get("/promo/reset-usage/{usage_id}")
async def reset_promo_usage(request: Request, usage_id: int):
    """Удалить использование промокода (сбросить для пользователя)"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    async with aiosqlite.connect(JARVIS_DB_PATH) as db:
        # Получаем promo_code_id перед удалением
        cursor = await db.execute(
            "SELECT promo_code_id, user_id, subscription_id FROM promo_code_usages WHERE id = ?",
            (usage_id,)
        )
        row = await cursor.fetchone()

        if row:
            promo_code_id, user_id, subscription_id = row

            # Удаляем использование
            await db.execute("DELETE FROM promo_code_usages WHERE id = ?", (usage_id,))

            # Уменьшаем счётчик использований
            await db.execute(
                "UPDATE promo_codes SET current_uses = MAX(0, current_uses - 1) WHERE id = ?",
                (promo_code_id,)
            )

            # Удаляем подписку если была создана
            if subscription_id:
                await db.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))

            # Удаляем VPN ключи пользователя
            await db.execute("DELETE FROM tunnel_keys WHERE user_id = ?", (user_id,))

            await db.commit()

    return RedirectResponse(url="/promo", status_code=302)


@app.get("/promo/reset/{user_id}")
async def reset_promo_by_user(request: Request, user_id: int):
    """Сбросить промокод для пользователя по user_id"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    async with aiosqlite.connect(JARVIS_DB_PATH) as db:
        # Получаем последнее использование промокода для этого пользователя
        cursor = await db.execute(
            "SELECT id, promo_code_id, subscription_id FROM promo_code_usages WHERE user_id = ? ORDER BY used_at DESC LIMIT 1",
            (user_id,)
        )
        row = await cursor.fetchone()

        if row:
            usage_id, promo_code_id, subscription_id = row

            # Удаляем использование
            await db.execute("DELETE FROM promo_code_usages WHERE id = ?", (usage_id,))

            # Уменьшаем счётчик использований
            await db.execute(
                "UPDATE promo_codes SET current_uses = MAX(0, current_uses - 1) WHERE id = ?",
                (promo_code_id,)
            )

            # Удаляем подписку если была создана
            if subscription_id:
                await db.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))

            # Удаляем VPN ключи пользователя
            await db.execute("DELETE FROM tunnel_keys WHERE user_id = ?", (user_id,))

            await db.commit()

    return RedirectResponse(url="/", status_code=302)


@app.get("/user/delete/{user_id}")
async def delete_user(request: Request, user_id: int):
    """Полностью удалить пользователя из БД"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    async with aiosqlite.connect(JARVIS_DB_PATH) as db:
        # Удаляем все связанные данные
        await db.execute("DELETE FROM tunnel_keys WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM promo_code_usages WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM diary_entries WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM habits WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM memory_contexts WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM api_usage_logs WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM booking_links WHERE user_id = ?", (user_id,))
        # Удаляем самого пользователя
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()

    return RedirectResponse(url="/", status_code=302)


async def get_promo_stats():
    """Статистика промокодов"""
    data = {"promos": [], "usages": [], "error": None}

    try:
        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Промокоды с новыми полями
            cursor = await db.execute("""
                SELECT
                    id, code, promo_type, description,
                    plan, days,
                    discount_percent, discount_amount, discount_permanent,
                    applies_to_plans, min_months, new_users_only,
                    max_uses, max_uses_per_user, current_uses,
                    expires_at, is_active, created_at
                FROM promo_codes ORDER BY created_at DESC
            """)
            rows = await cursor.fetchall()
            for row in rows:
                data["promos"].append({
                    "id": row[0],
                    "code": row[1],
                    "promo_type": row[2] or "subscription",
                    "description": row[3],
                    "plan": row[4],
                    "days": row[5] or 0,
                    "discount_percent": row[6] or 0,
                    "discount_amount": row[7] or 0,
                    "discount_permanent": row[8],
                    "applies_to_plans": row[9],
                    "min_months": row[10] or 0,
                    "new_users_only": row[11],
                    "max_uses": row[12],
                    "max_uses_per_user": row[13] or 1,
                    "current_uses": row[14] or 0,
                    "expires_at": row[15][:10] if row[15] else None,
                    "is_active": row[16],
                    "created_at": row[17][:10] if row[17] else "-",
                })

            # Последние использования
            cursor = await db.execute("""
                SELECT
                    pcu.id,
                    pcu.used_at,
                    pc.code,
                    u.username,
                    u.first_name,
                    u.telegram_id
                FROM promo_code_usages pcu
                JOIN promo_codes pc ON pcu.promo_code_id = pc.id
                JOIN users u ON pcu.user_id = u.id
                ORDER BY pcu.used_at DESC
                LIMIT 30
            """)
            rows = await cursor.fetchall()
            for row in rows:
                username = row[3] if row[3] else row[4] if row[4] else f"ID:{row[5]}"
                data["usages"].append({
                    "id": row[0],
                    "used_at": row[1][:16].replace("T", " ") if row[1] else "-",
                    "code": row[2],
                    "username": username,
                    "telegram_id": row[5],
                })

    except Exception as e:
        data["error"] = str(e)

    return data


def render_promo_page(data: dict) -> str:
    """Рендер страницы промокодов"""
    error = data.get("error")

    # Типы промокодов для отображения
    PROMO_TYPE_LABELS = {
        "subscription": "📦 Подписка",
        "discount_percent": "💰 Скидка %",
        "discount_fixed": "💵 Фикс. скидка",
        "trial_extend": "⏰ Продление триала"
    }

    # Таблица промокодов
    promos_rows = ""
    for p in data.get("promos", []):
        status = "🟢" if p["is_active"] else "🔴"
        uses_text = f"{p['current_uses']}"
        if p["max_uses"]:
            uses_text += f" / {p['max_uses']}"
        else:
            uses_text += " / ∞"

        # Форматируем тип и значение
        promo_type = p.get("promo_type", "subscription")
        type_label = PROMO_TYPE_LABELS.get(promo_type, promo_type)

        # Значение в зависимости от типа
        if promo_type == "subscription":
            plan_upper = (p.get("plan") or "basic").upper()
            days = p.get("days", 0)
            days_text = "навсегда" if days == 0 else f"{days} дн."
            value_text = f"{plan_upper} / {days_text}"
        elif promo_type == "discount_percent":
            perm = " 🔄" if p.get("discount_permanent") else ""
            value_text = f"-{p.get('discount_percent', 0)}%{perm}"
        elif promo_type == "discount_fixed":
            value_text = f"-{(p.get('discount_amount', 0) or 0) // 100}₽"
        elif promo_type == "trial_extend":
            value_text = f"+{p.get('days', 0)} дн."
        else:
            value_text = "-"

        # Ограничения
        restrictions = []
        if p.get("new_users_only"):
            restrictions.append("👤 новые")
        if p.get("expires_at"):
            restrictions.append(f"до {p['expires_at']}")
        restrictions_text = "<br>".join(restrictions) if restrictions else "-"

        promos_rows += f"""
        <tr>
            <td>{status}</td>
            <td><code>{esc(p['code'])}</code></td>
            <td>{esc(type_label)}</td>
            <td>{esc(value_text)}</td>
            <td>{esc(p['description'])}</td>
            <td style="font-size:11px">{restrictions_text}</td>
            <td>{uses_text}</td>
            <td>
                <a href="/promo/toggle/{p['id']}" class="btn-small">{'Откл' if p['is_active'] else 'Вкл'}</a>
                <a href="/promo/delete/{p['id']}" class="btn-small btn-danger" onclick="return confirm('Удалить?')">✕</a>
            </td>
        </tr>
        """

    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Промокоды — Admin</title>
    {COMMON_STYLES}
    <style>
        .form-row {{ display: flex; gap: 15px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }}
        .form-row label {{ color: #666; font-size: 13px; min-width: 100px; }}
        .form-row input, .form-row select {{ padding: 8px 12px; background: #fff; border: 1px solid #ddd; border-radius: 6px; color: #333; }}
        .form-row input[type="checkbox"] {{ width: 18px; height: 18px; }}
        .form-group {{ background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 15px; margin-bottom: 15px; }}
        .form-group h4 {{ color: #666; font-size: 12px; margin-bottom: 12px; text-transform: uppercase; }}
        .hidden {{ display: none !important; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎁 Промокоды</h1>
            <nav>
                <a href="/">Dashboard</a>
                <a href="/promo" class="active">Промокоды</a>
                <a href="/referrals">Рефералы</a>
                <a href="/logout">Выйти</a>
            </nav>
        </header>

        {"<div class='error'>Ошибка: " + str(error) + "</div>" if error else ""}

        <div class="section">
            <h2>Добавить промокод</h2>
            <form method="post" action="/promo/create" id="promoForm">
                <!-- Основные поля -->
                <div class="form-row">
                    <label>Код:</label>
                    <input type="text" name="code" placeholder="WELCOME50" required style="text-transform: uppercase; width: 150px;">

                    <label>Тип:</label>
                    <select name="promo_type" id="promoType" onchange="updateFormFields()" required>
                        <option value="subscription">📦 Подписка VPN</option>
                        <option value="discount_percent">💰 Скидка %</option>
                        <option value="discount_fixed">💵 Фикс. скидка</option>
                        <option value="trial_extend">⏰ Продление триала</option>
                    </select>

                    <label>Описание:</label>
                    <input type="text" name="description" placeholder="Скидка 50% для новых" required style="flex: 1; min-width: 200px;">
                </div>

                <!-- Для подписки -->
                <div class="form-group" id="subscriptionGroup">
                    <h4>Подписка VPN</h4>
                    <div class="form-row">
                        <label>План:</label>
                        <select name="plan" style="width: 120px;">
                            <option value="basic">Basic</option>
                            <option value="standard">Standard</option>
                            <option value="pro">Pro</option>
                        </select>

                        <label>Срок:</label>
                        <select name="days" style="width: 130px;">
                            <option value="7">7 дней</option>
                            <option value="14">14 дней</option>
                            <option value="30" selected>30 дней</option>
                            <option value="90">90 дней</option>
                            <option value="0">Навсегда</option>
                        </select>

                        <label style="margin-left: 20px;">
                            <input type="checkbox" name="new_users_only" value="1">
                            Только новые пользователи
                        </label>
                    </div>
                </div>

                <!-- Для скидки % -->
                <div class="form-group hidden" id="discountPercentGroup">
                    <h4>Настройки скидки</h4>
                    <div class="form-row">
                        <label>Скидка %:</label>
                        <input type="number" name="discount_percent" value="10" min="1" max="100" style="width: 80px;">

                        <label style="margin-left: 20px;">
                            <input type="checkbox" name="discount_permanent" value="1">
                            Постоянная скидка 🔄
                        </label>
                    </div>
                </div>

                <!-- Для фикс. скидки -->
                <div class="form-group hidden" id="discountFixedGroup">
                    <h4>Фиксированная скидка</h4>
                    <div class="form-row">
                        <label>Скидка ₽:</label>
                        <input type="number" name="discount_amount" value="100" min="1" style="width: 100px;">
                    </div>
                </div>

                <!-- Для триала -->
                <div class="form-group hidden" id="trialGroup">
                    <h4>Продление триала</h4>
                    <div class="form-row">
                        <label>Срок:</label>
                        <select name="trial_days" style="width: 130px;">
                            <option value="7" selected>7 дней</option>
                            <option value="14">14 дней</option>
                            <option value="30">30 дней</option>
                        </select>
                    </div>
                </div>

                <!-- Лимиты -->
                <div class="form-group">
                    <h4>Лимиты</h4>
                    <div class="form-row">
                        <label>Всего исп.:</label>
                        <input type="number" name="max_uses" placeholder="∞" min="1" style="width: 80px;">

                        <label>На юзера:</label>
                        <input type="number" name="max_uses_per_user" value="1" min="1" style="width: 60px;">

                        <label>Истекает через:</label>
                        <input type="number" name="expires_days" value="0" min="0" style="width: 60px;"> дней
                    </div>
                </div>

                <button type="submit" class="btn">Создать промокод</button>
            </form>
        </div>

        <div class="section">
            <h2>Все промокоды</h2>
            <table>
                <thead>
                    <tr>
                        <th>Статус</th><th>Код</th><th>Тип</th><th>Значение</th>
                        <th>Описание</th><th>Ограничения</th><th>Использований</th><th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    {promos_rows if promos_rows else "<tr><td colspan='8' class='empty'>Нет промокодов</td></tr>"}
                </tbody>
            </table>
        </div>

        <p class="footer">Обновлено: {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
    </div>

    <script>
        function updateFormFields() {{
            const type = document.getElementById('promoType').value;

            // Скрываем все группы
            document.getElementById('subscriptionGroup').classList.add('hidden');
            document.getElementById('discountPercentGroup').classList.add('hidden');
            document.getElementById('discountFixedGroup').classList.add('hidden');
            document.getElementById('trialGroup').classList.add('hidden');

            // Показываем нужную группу
            if (type === 'subscription') {{
                document.getElementById('subscriptionGroup').classList.remove('hidden');
            }} else if (type === 'discount_percent') {{
                document.getElementById('discountPercentGroup').classList.remove('hidden');
            }} else if (type === 'discount_fixed') {{
                document.getElementById('discountFixedGroup').classList.remove('hidden');
            }} else if (type === 'trial_extend') {{
                document.getElementById('trialGroup').classList.remove('hidden');
            }}
        }}

        // Инициализация при загрузке
        document.addEventListener('DOMContentLoaded', updateFormFields);
    </script>
</body>
</html>
    """


# === VPN USERS (из БД - Xray) ===

async def get_vpn_users():
    """Получить список VPN пользователей из БД"""
    users = []
    error = None

    try:
        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Получаем пользователей с VPN ключами
            cursor = await db.execute("""
                SELECT
                    tk.id,
                    tk.xray_email,
                    tk.device_name,
                    tk.subscription_url,
                    tk.is_active,
                    tk.created_at,
                    u.telegram_id,
                    u.username,
                    u.first_name,
                    u.vpn_trial_used,
                    u.vpn_trial_expires,
                    s.plan,
                    s.expires_at as sub_expires
                FROM tunnel_keys tk
                JOIN users u ON tk.user_id = u.id
                LEFT JOIN subscriptions s ON s.user_id = u.id AND s.status = 'active'
                ORDER BY tk.created_at DESC
            """)
            rows = await cursor.fetchall()

            for row in rows:
                # Определяем дату истечения (подписка или триал)
                expires_at = None
                if row["sub_expires"]:
                    expires_at = row["sub_expires"]
                elif row["vpn_trial_expires"]:
                    expires_at = row["vpn_trial_expires"]

                users.append({
                    "id": row["id"],
                    "device_name": row["device_name"],
                    "subscription_url": row["subscription_url"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"],
                    "telegram_id": row["telegram_id"],
                    "username": row["username"],
                    "first_name": row["first_name"],
                    "plan": row["plan"] or ("trial" if row["vpn_trial_used"] else None),
                    "expires_at": expires_at,
                })

    except Exception as e:
        error = str(e)

    return users, error


async def toggle_vpn_key(key_id: int):
    """Включить/отключить VPN ключ"""
    try:
        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            await db.execute("""
                UPDATE tunnel_keys
                SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
                WHERE id = ?
            """, (key_id,))
            await db.commit()
            return True
    except Exception:
        return False


async def delete_vpn_key(key_id: int):
    """Удалить VPN ключ из БД и с VPN сервера"""
    try:
        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            # Получаем информацию о ключе для удаления с сервера
            cursor = await db.execute(
                "SELECT xray_email FROM tunnel_keys WHERE id = ?",
                (key_id,)
            )
            row = await cursor.fetchone()

            if row and row[0]:
                username = row[0]  # Это email для Xray (user_XXX_dN)
                # Удаляем с VPN сервера через SSH
                await _delete_from_vpn_server(username)

            # Удаляем из БД
            await db.execute("DELETE FROM tunnel_keys WHERE id = ?", (key_id,))
            await db.commit()
            return True
    except Exception as e:
        print(f"Error deleting VPN key: {e}")
        return False


async def _delete_from_vpn_server(username: str):
    """Удалить пользователя с VPN сервера через SSH"""
    import asyncssh
    import json
    import shlex

    # Получаем конфиг VPN сервера из переменных окружения
    vpn_servers_json = os.getenv("VPN_SERVERS", "[]")
    try:
        servers = json.loads(vpn_servers_json)
        if not servers:
            print("No VPN servers configured")
            return

        server = servers[0]  # Берём первый сервер
        host = server.get("host")
        ssh_user = server.get("ssh_user", "root")
        ssh_password = server.get("ssh_password")
        ssh_port = server.get("ssh_port", 22)

        if not host or not ssh_password:
            print("VPN server credentials not configured")
            return

        # Подключаемся по SSH и удаляем пользователя
        safe_username = shlex.quote(username)
        cmd = f'/usr/local/bin/xray-user remove {safe_username}'

        async with asyncssh.connect(
            host,
            port=ssh_port,
            username=ssh_user,
            password=ssh_password,
            known_hosts=None
        ) as conn:
            result = await conn.run(cmd, check=False)
            output = result.stdout.strip()
            if output == "REMOVED":
                print(f"VPN: user {username} removed from server")
            else:
                print(f"VPN: remove result: {output}")

    except Exception as e:
        print(f"Error removing from VPN server: {e}")


@app.get("/vpn", response_class=HTMLResponse)
async def vpn_page(request: Request, sent: str = None, error: str = None):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    users, db_error = await get_vpn_users()
    # Объединяем ошибки
    display_error = db_error
    if error:
        error_messages = {
            "key_not_found": "Ключ не найден",
            "no_bot_token": "BOT_TOKEN не настроен",
            "send_failed": "Не удалось отправить сообщение",
            "exception": "Ошибка при отправке"
        }
        display_error = error_messages.get(error, error)

    success_message = "Ключ успешно отправлен пользователю!" if sent else None
    html = render_vpn_page(users, display_error, success_message)
    return HTMLResponse(html)


@app.get("/vpn/toggle/{key_id}")
async def vpn_toggle(request: Request, key_id: int):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await toggle_vpn_key(key_id)
    return RedirectResponse(url="/vpn", status_code=302)


@app.get("/vpn/delete/{key_id}")
async def vpn_delete(request: Request, key_id: int):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await delete_vpn_key(key_id)
    return RedirectResponse(url="/vpn", status_code=302)


@app.get("/vpn/send/{key_id}")
async def vpn_send_key(request: Request, key_id: int):
    """Отправить VPN ключ пользователю через Telegram бота"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    try:
        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            # Получаем информацию о ключе и пользователе
            cursor = await db.execute("""
                SELECT tk.subscription_url, tk.device_name, u.telegram_id
                FROM tunnel_keys tk
                JOIN users u ON tk.user_id = u.id
                WHERE tk.id = ?
            """, (key_id,))
            row = await cursor.fetchone()

            if not row or not row[0]:
                return RedirectResponse(url="/vpn?error=key_not_found", status_code=302)

            subscription_url = row[0]
            device_name = row[1] or "VPN"
            telegram_id = row[2]

            # Отправляем сообщение через Telegram Bot API
            import aiohttp
            bot_token = os.getenv("BOT_TOKEN")
            if not bot_token:
                return RedirectResponse(url="/vpn?error=no_bot_token", status_code=302)

            message = f"""🔑 <b>Ваш VPN ключ</b> ({device_name})

Скопируйте ссылку ниже и вставьте в приложение:

<code>{subscription_url}</code>

📱 Инструкция по подключению: /vpn_help"""

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": telegram_id,
                        "text": message,
                        "parse_mode": "HTML"
                    }
                ) as resp:
                    if resp.status == 200:
                        return RedirectResponse(url="/vpn?sent=1", status_code=302)
                    else:
                        return RedirectResponse(url="/vpn?error=send_failed", status_code=302)

    except Exception as e:
        print(f"Error sending VPN key: {e}")
        return RedirectResponse(url="/vpn?error=exception", status_code=302)


def render_vpn_page(users: list, error: str, success: str = None) -> str:
    """Рендер страницы VPN пользователей"""

    # Статистика
    total_users = len(users) if users else 0
    active_users = sum(1 for u in users if u.get("is_active")) if users else 0
    trial_users = sum(1 for u in users if u.get("plan") == "trial") if users else 0

    # Таблица пользователей
    users_rows = ""
    if users:
        for u in users:
            is_active = u.get("is_active")
            status_emoji = "🟢" if is_active else "🔴"

            # Имя пользователя для отображения
            tg_id = u.get("telegram_id", "")
            username = u.get("username") or u.get("first_name") or f"ID:{tg_id}"
            if tg_id:
                tg_display = f'<a href="tg://user?id={tg_id}" style="color:#0d6efd">{esc(username)}</a>'
            else:
                tg_display = esc(username)

            # Устройство
            device_name = u.get("device_name") or u.get("email") or "-"

            # План
            plan = u.get("plan")
            if plan == "trial":
                plan_text = "<span style='color:#6c757d'>Триал</span>"
            elif plan:
                plan_text = f"<span style='color:#28a745'>{esc(plan.upper())}</span>"
            else:
                plan_text = "-"

            # Дата истечения
            expires_at = u.get("expires_at")
            if expires_at:
                try:
                    if "T" in expires_at:
                        expire_date = datetime.fromisoformat(expires_at.replace("Z", ""))
                    else:
                        expire_date = datetime.strptime(expires_at[:10], "%Y-%m-%d")
                    days_left = (expire_date - datetime.now()).days
                    if days_left < 0:
                        expire_text = f"<span style='color:#dc3545'>Истёк</span>"
                    elif days_left <= 3:
                        expire_text = f"<span style='color:#ffc107'>{expire_date.strftime('%d.%m.%Y')} ({days_left}д)</span>"
                    else:
                        expire_text = f"{expire_date.strftime('%d.%m.%Y')} ({days_left}д)"
                except Exception:
                    expire_text = esc(expires_at[:10]) if expires_at else "-"
            else:
                expire_text = "♾️"

            # Дата создания
            created = u.get("created_at")
            if created:
                created_text = created[:10] if len(created) >= 10 else created
            else:
                created_text = "-"

            toggle_text = "Откл" if is_active else "Вкл"
            key_id = u.get("id")
            sub_url = u.get("subscription_url") or ""

            # Кнопка просмотра ключа (если есть URL)
            view_btn = ""
            send_btn = ""
            if sub_url:
                # Кнопка просмотра - показывает модальное окно с ключом
                view_btn = f'<a href="#" class="btn-small" onclick="showKey(\'{esc(sub_url)}\'); return false;" title="Показать ключ">👁</a>'
                # Кнопка отправки ключа пользователю
                send_btn = f'<a href="/vpn/send/{key_id}" class="btn-small btn-success" onclick="return confirm(\'Отправить ключ пользователю {esc(username)}?\')" title="Отправить ключ">📤</a>'

            users_rows += f"""
            <tr>
                <td>{status_emoji}</td>
                <td>{tg_display}</td>
                <td><code>{tg_id}</code></td>
                <td>{esc(device_name)}</td>
                <td>{plan_text}</td>
                <td>{expire_text}</td>
                <td>{created_text}</td>
                <td>
                    {view_btn}
                    {send_btn}
                    <a href="/vpn/toggle/{key_id}" class="btn-small">{toggle_text}</a>
                    <a href="/vpn/delete/{key_id}" class="btn-small btn-danger" onclick="return confirm('Удалить VPN ключ?')">✕</a>
                </td>
            </tr>
            """

    # Подсчёт платных подписок
    paid_users = sum(1 for u in users if u.get("plan") in ("basic", "standard", "pro")) if users else 0

    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VPN ключи — Admin</title>
    {COMMON_STYLES}
</head>
<body>
    <div class="container">
        <header>
            <h1>🔐 VPN ключи</h1>
            <nav>
                <a href="/">Dashboard</a>
                <a href="/vpn" class="active">VPN ключи</a>
                <a href="/promo">Промокоды</a>
                <a href="/referrals">Рефералы</a>
                <a href="/logout">Выйти</a>
            </nav>
        </header>

        {"<div class='error'>Ошибка: " + str(error) + "</div>" if error else ""}
        {"<div class='success'>✅ " + str(success) + "</div>" if success else ""}

        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-value">{total_users}</div>
                <div class="stat-label">Всего подписок</div>
            </div>
            <div class="stat-card green">
                <div class="stat-value">{paid_users}</div>
                <div class="stat-label">Платных</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">{trial_users}</div>
                <div class="stat-label">На триале</div>
            </div>
            <div class="stat-card" style="background: #e8f5e9;">
                <div class="stat-value">{active_users}</div>
                <div class="stat-label">VPN активен</div>
            </div>
        </div>

        <div class="section">
            <h2>Все VPN ключи</h2>
            <table>
                <thead>
                    <tr>
                        <th title="Статус ключа">VPN</th>
                        <th>Пользователь</th>
                        <th>Telegram ID</th>
                        <th>Устройство</th>
                        <th title="План подписки">💳</th>
                        <th title="Дней до истечения">⏰</th>
                        <th>Создан</th>
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    {users_rows if users_rows else "<tr><td colspan='8' class='empty'>Нет VPN ключей</td></tr>"}
                </tbody>
            </table>
        </div>

        <p class="footer">
            👁 Показать ключ • 📤 Отправить ключ пользователю •
            Обновлено: {datetime.now().strftime("%d.%m.%Y %H:%M")}
        </p>
    </div>

    <!-- Модальное окно для просмотра ключа -->
    <div id="keyModal" class="modal" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <h3>🔑 VPN Ключ</h3>
            <textarea id="keyText" readonly rows="3"></textarea>
            <div class="modal-buttons">
                <button onclick="copyKey()" class="btn">📋 Копировать</button>
                <button onclick="closeModal()" class="btn btn-secondary">Закрыть</button>
            </div>
        </div>
    </div>

    <style>
        .btn-success {{ color: #28a745 !important; border-color: #a3d9a5 !important; }}
        .btn-success:hover {{ background: #f0fff4; }}
        .btn-secondary {{ background: #6c757d; }}
        .btn-secondary:hover {{ background: #5a6268; }}

        .modal {{
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }}
        .modal.show {{ display: flex; }}
        .modal-content {{
            background: #fff;
            padding: 24px;
            border-radius: 12px;
            width: 90%;
            max-width: 600px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
        }}
        .modal-content h3 {{ margin-bottom: 16px; color: #333; }}
        .modal-content textarea {{
            width: 100%;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-family: monospace;
            font-size: 12px;
            resize: none;
            background: #f8f9fa;
        }}
        .modal-buttons {{
            display: flex;
            gap: 10px;
            margin-top: 16px;
            justify-content: flex-end;
        }}
    </style>

    <script>
        function showKey(key) {{
            document.getElementById('keyText').value = key;
            document.getElementById('keyModal').classList.add('show');
        }}

        function closeModal(event) {{
            if (!event || event.target.classList.contains('modal')) {{
                document.getElementById('keyModal').classList.remove('show');
            }}
        }}

        function copyKey() {{
            const textarea = document.getElementById('keyText');
            textarea.select();
            document.execCommand('copy');
            alert('Ключ скопирован!');
        }}

        // Закрытие по Escape
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeModal();
        }});
    </script>
</body>
</html>
    """


# === JARVIS STATS ===

async def get_jarvis_stats():
    """Статистика Jarvis Bot"""
    data = {
        "summary": {},
        "users": [],
        "api_by_type": [],
        "features": [],
        "error": None,
    }

    try:
        async with aiosqlite.connect(JARVIS_DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            today = datetime.now().date().isoformat()
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()

            # Общее
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            data["summary"]["total_users"] = (await cursor.fetchone())[0]

            cursor = await db.execute("SELECT COUNT(*) FROM users WHERE calendar_connected = 1")
            data["summary"]["calendars_connected"] = (await cursor.fetchone())[0]

            # API за сегодня
            cursor = await db.execute("""
                SELECT COUNT(*), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(estimated_cost_cents), 0)
                FROM api_usage_logs WHERE date(created_at) = ?
            """, (today,))
            row = await cursor.fetchone()
            data["summary"]["today_requests"] = row[0]
            data["summary"]["today_tokens"] = row[1]
            data["summary"]["today_cost"] = round(row[2] / 100, 2)

            # API за неделю
            cursor = await db.execute("""
                SELECT COUNT(*), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(estimated_cost_cents), 0)
                FROM api_usage_logs WHERE created_at > ?
            """, (week_ago,))
            row = await cursor.fetchone()
            data["summary"]["week_requests"] = row[0]
            data["summary"]["week_tokens"] = row[1]
            data["summary"]["week_cost"] = round(row[2] / 100, 2)

            # API за всё время
            cursor = await db.execute("""
                SELECT COUNT(*), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(estimated_cost_cents), 0)
                FROM api_usage_logs
            """)
            row = await cursor.fetchone()
            data["summary"]["total_requests"] = row[0]
            data["summary"]["total_tokens"] = row[1]
            data["summary"]["total_cost"] = round(row[2] / 100, 2)

            # Пользователи с использованием функций + VPN + подписки + промокоды
            cursor = await db.execute("""
                SELECT
                    u.id,
                    u.telegram_id,
                    u.username,
                    u.first_name,
                    u.calendar_connected,
                    u.created_at,
                    (SELECT MAX(created_at) FROM conversations WHERE user_id = u.id AND role = 'user') as last_activity,
                    (SELECT COUNT(*) FROM api_usage_logs WHERE user_id = u.id) as requests,
                    (SELECT COALESCE(SUM(total_tokens), 0) FROM api_usage_logs WHERE user_id = u.id) as tokens,
                    (SELECT COALESCE(SUM(estimated_cost_cents), 0) FROM api_usage_logs WHERE user_id = u.id) as cost_cents,
                    (SELECT COUNT(*) FROM conversations WHERE user_id = u.id AND role = 'user') as messages,
                    (SELECT COUNT(*) FROM tasks WHERE user_id = u.id) as tasks,
                    (SELECT COUNT(*) FROM diary_entries WHERE user_id = u.id) as diary,
                    (SELECT COUNT(*) FROM reminders WHERE user_id = u.id) as reminders,
                    (SELECT COUNT(*) FROM habits WHERE user_id = u.id AND is_active = 1) as habits,
                    (SELECT COUNT(*) FROM tunnel_keys WHERE user_id = u.id) as vpn_keys_total,
                    (SELECT COUNT(*) FROM tunnel_keys WHERE user_id = u.id AND is_active = 1) as vpn_keys_active,
                    (SELECT plan FROM subscriptions WHERE user_id = u.id AND status = 'active' ORDER BY expires_at DESC LIMIT 1) as sub_plan,
                    (SELECT expires_at FROM subscriptions WHERE user_id = u.id AND status = 'active' ORDER BY expires_at DESC LIMIT 1) as sub_expires,
                    u.vpn_trial_used,
                    u.vpn_trial_expires,
                    (SELECT pc.code FROM promo_code_usages pcu JOIN promo_codes pc ON pcu.promo_code_id = pc.id WHERE pcu.user_id = u.id ORDER BY pcu.used_at DESC LIMIT 1) as promo_code
                FROM users u
                ORDER BY messages DESC
            """)
            rows = await cursor.fetchall()
            for row in rows:
                username = row[2]
                if username:
                    display_name = f"@{username}"
                elif row[3]:
                    display_name = row[3]
                else:
                    display_name = f"ID: {row[1]}"

                # Определяем план подписки
                # Индексы: 0-id, 1-tg_id, 2-username, 3-first_name, 4-calendar, 5-created_at
                # 6-last_activity, 7-requests, 8-tokens, 9-cost, 10-messages, 11-tasks
                # 12-diary, 13-reminders, 14-habits, 15-vpn_total, 16-vpn_active
                # 17-sub_plan, 18-sub_expires, 19-vpn_trial_used, 20-vpn_trial_expires, 21-promo_code
                sub_plan = row[17]
                sub_expires = row[18]
                vpn_trial_used = row[19]
                vpn_trial_expires = row[20]
                promo_code = row[21]

                if sub_plan:
                    plan = sub_plan
                    expires = sub_expires
                elif vpn_trial_used and vpn_trial_expires:
                    plan = "trial"
                    expires = vpn_trial_expires
                else:
                    plan = None
                    expires = None

                data["users"].append({
                    "id": row[0],
                    "telegram_id": row[1],
                    "username": display_name,
                    "raw_username": row[2],
                    "first_name": row[3] or "-",
                    "calendar": row[4],
                    "created_at": row[5][:10] if row[5] else "-",
                    "last_activity": row[6][:16].replace("T", " ") if row[6] else "-",
                    "requests": row[7] or 0,
                    "tokens": row[8] or 0,
                    "cost": round(row[9] / 100, 2) if row[9] else 0,
                    "messages": row[10] or 0,
                    "tasks": row[11] or 0,
                    "diary": row[12] or 0,
                    "reminders": row[13] or 0,
                    "habits": row[14] or 0,
                    "vpn_keys_total": row[15] or 0,
                    "vpn_keys_active": row[16] or 0,
                    "plan": plan,
                    "expires": expires,
                    "promo_code": promo_code,
                    "is_blocked": False,  # TODO: добавить колонку в БД
                    "vpn_keys": [],  # будет заполнено ниже
                })

            # Получаем VPN ключи для всех пользователей
            cursor = await db.execute("""
                SELECT tk.id, tk.user_id, tk.device_name, tk.subscription_url, tk.is_active, tk.created_at
                FROM tunnel_keys tk
                ORDER BY tk.created_at DESC
            """)
            vpn_rows = await cursor.fetchall()
            # Группируем ключи по user_id
            vpn_by_user = {}
            for vr in vpn_rows:
                user_id = vr[1]
                if user_id not in vpn_by_user:
                    vpn_by_user[user_id] = []
                vpn_by_user[user_id].append({
                    "id": vr[0],
                    "device_name": vr[2] or "VPN",
                    "subscription_url": vr[3],
                    "is_active": vr[4],
                    "created_at": vr[5][:10] if vr[5] else "-",
                })
            # Добавляем ключи к пользователям
            for u in data["users"]:
                u["vpn_keys"] = vpn_by_user.get(u["id"], [])

            # API по типам
            cursor = await db.execute("""
                SELECT
                    api_type, model,
                    COUNT(*) as requests,
                    SUM(total_tokens) as tokens,
                    SUM(estimated_cost_cents) as cost_cents,
                    ROUND(AVG(response_time_ms), 0) as avg_time
                FROM api_usage_logs
                GROUP BY api_type, model
                ORDER BY tokens DESC
            """)
            rows = await cursor.fetchall()
            for row in rows:
                data["api_by_type"].append({
                    "type": row[0],
                    "model": row[1],
                    "requests": row[2],
                    "tokens": row[3] or 0,
                    "cost": round((row[4] or 0) / 100, 2),
                    "avg_time": int(row[5] or 0),
                })

            # Напоминания
            cursor = await db.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN is_sent = 1 THEN 1 ELSE 0 END) as sent,
                       SUM(CASE WHEN is_sent = 0 AND remind_at > datetime('now') THEN 1 ELSE 0 END) as pending
                FROM reminders
            """)
            row = await cursor.fetchone()
            data["reminders"] = {
                "total": row[0] or 0,
                "sent": row[1] or 0,
                "pending": row[2] or 0,
            }

            # VPN пользователи
            cursor = await db.execute("SELECT COUNT(*) FROM tunnel_keys WHERE is_active = 1")
            data["vpn_users"] = (await cursor.fetchone())[0]

            # Статистика подписок
            cursor = await db.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN plan IN ('basic', 'standard', 'pro') AND status = 'active' THEN 1 ELSE 0 END) as paid,
                    SUM(CASE WHEN plan = 'free_trial' AND status = 'active' THEN 1 ELSE 0 END) as trial,
                    SUM(CASE WHEN expires_at < datetime('now') THEN 1 ELSE 0 END) as expired
                FROM subscriptions
            """)
            row = await cursor.fetchone()
            data["subscriptions"] = {
                "total": row[0] or 0,
                "paid": row[1] or 0,
                "trial": row[2] or 0,
                "expired": row[3] or 0,
            }

            # Подписки по планам
            cursor = await db.execute("""
                SELECT plan, COUNT(*) as count
                FROM subscriptions
                WHERE status = 'active' AND (expires_at IS NULL OR expires_at > datetime('now'))
                GROUP BY plan
                ORDER BY count DESC
            """)
            rows = await cursor.fetchall()
            data["subscriptions_by_plan"] = [{"plan": row[0], "count": row[1]} for row in rows]

            # Функции
            cursor = await db.execute("""
                SELECT 'Сообщения' as feature, COUNT(*) as count FROM conversations WHERE role = 'user'
                UNION ALL SELECT 'Задачи', COUNT(*) FROM tasks
                UNION ALL SELECT 'Дневник', COUNT(*) FROM diary_entries
                UNION ALL SELECT 'Напоминания', COUNT(*) FROM reminders
                ORDER BY count DESC
            """)
            rows = await cursor.fetchall()
            for row in rows:
                data["features"].append({"name": row[0], "count": row[1]})

            # Привычки с детализацией
            cursor = await db.execute("""
                SELECT h.name, h.emoji, COUNT(hl.id) as count
                FROM habits h
                LEFT JOIN habit_logs hl ON h.id = hl.habit_id
                WHERE h.is_active = 1
                GROUP BY h.id, h.name, h.emoji
                ORDER BY count DESC
            """)
            rows = await cursor.fetchall()
            habits_detail = []
            total_habits = 0
            for row in rows:
                habits_detail.append({
                    "name": row[0],
                    "emoji": row[1],
                    "count": row[2]
                })
                total_habits += row[2]
            data["habits_detail"] = habits_detail
            data["habits_total"] = total_habits

    except Exception as e:
        data["error"] = str(e)

    return data


def render_jarvis_dashboard(data: dict) -> str:
    """Рендер страницы Dashboard с раскрывающимися строками"""
    error = data.get("error")
    users = data.get("users", [])

    # Подсчёт итогов
    total_users = len(users)
    total_paid = sum(1 for u in users if u.get('plan') in ('basic', 'standard', 'pro'))
    total_trial = sum(1 for u in users if u.get('plan') == 'trial')
    total_vpn = sum(u.get('vpn_keys_active', 0) for u in users)
    total_cost = sum(u.get('cost', 0) for u in users)

    # Генерация строк пользователей
    users_rows = ""
    for i, u in enumerate(users, 1):
        tg_id = u['telegram_id']
        username = u['username']

        # Ссылка на Telegram
        tg_link = f'<a href="tg://user?id={tg_id}" class="user-link">{esc(username)}</a>'

        # План подписки
        plan = u.get('plan')
        if plan == 'trial':
            plan_badge = '<span class="badge badge-gray">Триал</span>'
        elif plan in ('basic', 'standard', 'pro'):
            plan_badge = f'<span class="badge badge-green">{plan.upper()}</span>'
        else:
            plan_badge = '<span class="badge badge-none">—</span>'

        # Промокод с кнопкой сброса
        promo = u.get('promo_code')
        user_id = u['id']
        if promo:
            promo_text = f'<code>{esc(promo)}</code> <a href="/promo/reset/{user_id}" class="action-btn action-danger" onclick="event.stopPropagation(); return confirm(\'Сбросить промокод?\')" title="Сбросить">✕</a>'
        else:
            promo_text = '—'

        # Календарь
        calendar_icon = '✅' if u.get('calendar') else '❌'

        # VPN ключи
        vpn_total = u.get('vpn_keys_total', 0)
        vpn_active = u.get('vpn_keys_active', 0)

        # Дата истечения (дата + дней)
        expires = u.get('expires')
        expire_date_str = '—'
        days_left = None
        if expires:
            try:
                if "T" in str(expires):
                    expire_date = datetime.fromisoformat(str(expires).replace("Z", ""))
                else:
                    expire_date = datetime.strptime(str(expires)[:10], "%Y-%m-%d")
                days_left = (expire_date - datetime.now()).days
                expire_date_str = expire_date.strftime("%d.%m.%Y")
                if days_left < 0:
                    expire_text = f'{expire_date_str} <span class="text-danger">(истёк)</span>'
                elif days_left <= 3:
                    expire_text = f'{expire_date_str} <span class="text-warning">({days_left}д)</span>'
                else:
                    expire_text = f'{expire_date_str} ({days_left}д)'
            except Exception:
                expire_text = '—'
        else:
            expire_text = '—'

        # Заблокирован?
        blocked_class = ' user-blocked' if u.get('is_blocked') else ''

        # VPN ключи для детальной панели
        vpn_keys = u.get('vpn_keys', [])
        vpn_keys_html = ""
        if vpn_keys:
            for vk in vpn_keys:
                status = '🟢' if vk['is_active'] else '🔴'
                sub_url = vk.get('subscription_url', '')
                key_id = vk['id']

                # Кнопки действий для каждого ключа
                view_btn = f'<a href="#" class="action-btn" onclick="showKey(\'{esc(sub_url)}\'); return false;" title="Показать ключ">👁</a>' if sub_url else ''
                send_btn = f'<a href="/vpn/send/{key_id}" class="action-btn" onclick="return confirm(\'Отправить ключ?\')" title="Отправить">📤</a>' if sub_url else ''
                toggle_btn = f'<a href="/vpn/toggle/{key_id}" class="action-btn" title="Вкл/Откл">{"⏸" if vk["is_active"] else "▶"}</a>'
                delete_btn = f'<a href="/vpn/delete/{key_id}" class="action-btn action-danger" onclick="return confirm(\'Удалить ключ?\')" title="Удалить">🗑</a>'

                vpn_keys_html += f'''
                <div class="vpn-key-row">
                    <span>{status} {esc(vk["device_name"])}</span>
                    <span class="vpn-key-actions">{view_btn}{send_btn}{toggle_btn}{delete_btn}</span>
                </div>'''
        else:
            vpn_keys_html = '<div class="no-data">Нет VPN ключей</div>'

        # Действия с пользователем (удаление)
        delete_action = f'<a href="/user/delete/{user_id}" class="action-btn action-danger" onclick="return confirm(\'Удалить пользователя? Все данные будут удалены!\')" title="Удалить">🗑</a>'

        users_rows += f'''
        <tr class="user-row{blocked_class}" onclick="toggleRow({i})">
            <td>{i}</td>
            <td>{tg_link}</td>
            <td>{plan_badge}</td>
            <td>{promo_text}</td>
            <td>{expire_text}</td>
            <td class="expand-icon" id="expand-{i}">▼</td>
            <td onclick="event.stopPropagation();">{delete_action}</td>
        </tr>
        <tr class="detail-row" id="detail-{i}" style="display: none;">
            <td colspan="7">
                <div class="detail-panel">
                    <div class="detail-section">
                        <h4>🔐 VPN ключи ({vpn_active}/{vpn_total})</h4>
                        {vpn_keys_html}
                    </div>
                    <div class="detail-section detail-section-2col">
                        <h4>📊 Статистика</h4>
                        <div class="stats-grid">
                            <div class="detail-info"><span>💬 Сообщений:</span> {u['messages']}</div>
                            <div class="detail-info"><span>📋 Задач:</span> {u['tasks']}</div>
                            <div class="detail-info"><span>📓 Дневник:</span> {u['diary']}</div>
                            <div class="detail-info"><span>🔔 Напоминаний:</span> {u['reminders']}</div>
                            <div class="detail-info"><span>✅ Привычек:</span> {u['habits']}</div>
                            <div class="detail-info"><span>🤖 AI запросов:</span> {u['requests']:,}</div>
                            <div class="detail-info"><span>🔤 Токенов:</span> {u['tokens']:,}</div>
                            <div class="detail-info"><span>💰 Стоимость:</span> ${u['cost']:.2f}</div>
                        </div>
                    </div>
                    <div class="detail-section">
                        <h4>📋 Детали</h4>
                        <div class="detail-info">
                            <span>Telegram ID:</span> <code>{tg_id}</code>
                        </div>
                        <div class="detail-info">
                            <span>📅 Календарь:</span> {calendar_icon}
                        </div>
                        <div class="detail-info">
                            <span>Зарегистрирован:</span> {u.get("created_at", "—")}
                        </div>
                        <div class="detail-info">
                            <span>Последняя активность:</span> {u.get("last_activity", "—")}
                        </div>
                    </div>
                </div>
            </td>
        </tr>
        '''

    # Таблица API
    api_rows = ""
    for a in data.get("api_by_type", []):
        api_rows += f'''
        <tr>
            <td>{a['type']}</td>
            <td><code>{a['model']}</code></td>
            <td>{a['requests']:,}</td>
            <td>{a['tokens']:,}</td>
            <td>${a['cost']:.2f}</td>
        </tr>
        '''

    return f'''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard — Admin</title>
    {COMMON_STYLES}
    {DASHBOARD_STYLES}
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Dashboard</h1>
            <nav>
                <a href="/" class="active">Dashboard</a>
                <a href="/promo">Промокоды</a>
                <a href="/referrals">Рефералы</a>
                <a href="/logout">Выйти</a>
            </nav>
        </header>

        {"<div class='error'>Ошибка: " + str(error) + "</div>" if error else ""}

        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-value">{total_users}</div>
                <div class="stat-label">Пользователей</div>
            </div>
            <div class="stat-card green">
                <div class="stat-value">{total_paid}</div>
                <div class="stat-label">Платных</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">{total_trial}</div>
                <div class="stat-label">Триал</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{total_vpn}</div>
                <div class="stat-label">VPN активно</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${total_cost:.2f}</div>
                <div class="stat-label">Затраты AI</div>
            </div>
        </div>

        <div class="section">
            <h2>Пользователи <span class="section-hint">(клик для деталей)</span></h2>
            <table class="users-table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Пользователь</th>
                        <th title="Тарифный план">Тариф</th>
                        <th title="Использованный промокод">Промокод</th>
                        <th title="Дата истечения подписки">Истекает</th>
                        <th></th>
                        <th title="Действия">⚡</th>
                    </tr>
                </thead>
                <tbody>
                    {users_rows if users_rows else "<tr><td colspan='7' class='empty'>Нет пользователей</td></tr>"}
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>API использование</h2>
            <table>
                <thead>
                    <tr><th>Тип</th><th>Модель</th><th>Запросы</th><th>Токены</th><th>Стоимость</th></tr>
                </thead>
                <tbody>
                    {api_rows if api_rows else "<tr><td colspan='5' class='empty'>Нет данных</td></tr>"}
                </tbody>
            </table>
        </div>

        <p class="footer">Обновлено: {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
    </div>

    <!-- Модальное окно для VPN ключа -->
    <div id="keyModal" class="modal" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <h3>🔑 VPN Ключ</h3>
            <textarea id="keyText" readonly rows="3"></textarea>
            <div class="modal-buttons">
                <button onclick="copyKey()" class="btn">📋 Копировать</button>
                <button onclick="closeModal()" class="btn btn-secondary">Закрыть</button>
            </div>
        </div>
    </div>

    <script>
        function toggleRow(i) {{
            const detail = document.getElementById('detail-' + i);
            const expand = document.getElementById('expand-' + i);
            if (detail.style.display === 'none') {{
                detail.style.display = 'table-row';
                expand.textContent = '▲';
            }} else {{
                detail.style.display = 'none';
                expand.textContent = '▼';
            }}
        }}

        function showKey(key) {{
            document.getElementById('keyText').value = key;
            document.getElementById('keyModal').classList.add('show');
        }}

        function closeModal(event) {{
            if (!event || event.target.classList.contains('modal')) {{
                document.getElementById('keyModal').classList.remove('show');
            }}
        }}

        function copyKey() {{
            const textarea = document.getElementById('keyText');
            textarea.select();
            document.execCommand('copy');
            alert('Ключ скопирован!');
        }}

        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeModal();
        }});
    </script>
</body>
</html>
    '''


# Стили для Dashboard
DASHBOARD_STYLES = """
<style>
    .users-table { width: 100%; }
    .user-row { cursor: pointer; transition: background 0.2s; }
    .user-row:hover { background: #f0f4ff; }
    .user-row.user-blocked { background: #fff5f5; }
    .user-link { color: #0d6efd; text-decoration: none; font-weight: 500; }
    .user-link:hover { text-decoration: underline; }

    .badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .badge-green { background: #d4edda; color: #155724; }
    .badge-gray { background: #e9ecef; color: #6c757d; }
    .badge-none { color: #999; }

    .vpn-count { font-weight: 500; }
    .text-danger { color: #dc3545; }
    .text-warning { color: #ffc107; }
    .cost { color: #6c757d; font-size: 12px; }

    .stats-cell { font-size: 12px; white-space: nowrap; }
    .expand-icon { color: #999; font-size: 12px; text-align: center; }

    .detail-row td { padding: 0 !important; background: #f8f9fa; }
    .detail-panel { display: flex; gap: 30px; padding: 20px; flex-wrap: wrap; }
    .detail-section { flex: 1; min-width: 200px; }
    .detail-section h4 { color: #666; font-size: 13px; margin-bottom: 12px; border-bottom: 1px solid #e0e0e0; padding-bottom: 8px; }

    .vpn-key-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #eee; }
    .vpn-key-row:last-child { border-bottom: none; }
    .vpn-key-actions { display: flex; gap: 5px; }

    .action-btn { padding: 4px 8px; text-decoration: none; border-radius: 4px; font-size: 14px; transition: background 0.2s; }
    .action-btn:hover { background: #e9ecef; }
    .action-danger:hover { background: #fff5f5; }

    .detail-info { padding: 6px 0; color: #666; font-size: 13px; }
    .detail-info span { color: #999; }

    .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 20px; }
    .detail-section-2col { min-width: 300px; }

    .user-actions { display: flex; gap: 10px; flex-wrap: wrap; }
    .no-data { color: #999; font-size: 13px; font-style: italic; }

    .section-hint { color: #999; font-size: 12px; font-weight: normal; }

    .modal { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center; }
    .modal.show { display: flex; }
    .modal-content { background: #fff; padding: 24px; border-radius: 12px; width: 90%; max-width: 600px; }
    .modal-content h3 { margin-bottom: 16px; }
    .modal-content textarea { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 8px; font-family: monospace; font-size: 12px; resize: none; background: #f8f9fa; }
    .modal-buttons { display: flex; gap: 10px; margin-top: 16px; justify-content: flex-end; }
    .btn-secondary { background: #6c757d; }
</style>
"""


# === COMMON STYLES (LIGHT THEME) ===
COMMON_STYLES = """
<style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #f5f7fa;
        color: #333;
        min-height: 100vh;
    }
    .container { max-width: 1400px; margin: 0 auto; padding: 20px; }

    header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 20px 0;
        border-bottom: 1px solid #e0e0e0;
        margin-bottom: 30px;
    }
    header h1 { font-size: 24px; color: #333; font-weight: 600; }
    nav { display: flex; gap: 8px; }
    nav a {
        color: #666;
        text-decoration: none;
        padding: 8px 16px;
        border: 1px solid #ddd;
        border-radius: 8px;
        background: #fff;
        transition: all 0.2s;
    }
    nav a:hover { border-color: #0d6efd; color: #0d6efd; }
    nav a.active { background: #0d6efd; color: #fff; border-color: #0d6efd; }

    .stats-row { display: flex; gap: 20px; margin-bottom: 20px; }
    .stat-card {
        background: #fff;
        border: 1px solid #e0e0e0;
        border-radius: 12px;
        padding: 24px;
        flex: 1;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    }
    .stat-value { font-size: 32px; font-weight: 700; color: #333; }
    .stat-label { font-size: 13px; color: #888; margin-top: 5px; }
    .stat-card.green .stat-value { color: #28a745; }
    .stat-card.blue .stat-value { color: #0d6efd; }

    .section {
        background: #fff;
        border: 1px solid #e0e0e0;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    }
    .section h2 {
        font-size: 16px;
        color: #666;
        margin-bottom: 16px;
        padding-bottom: 12px;
        border-bottom: 1px solid #eee;
        font-weight: 600;
    }

    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
    th { color: #888; font-weight: 500; font-size: 12px; text-transform: uppercase; }
    tr:hover { background: #f8f9fa; }
    code { background: #f1f3f4; padding: 2px 6px; border-radius: 4px; font-size: 12px; color: #d63384; }
    .empty { text-align: center; color: #999; }

    /* Раскрывающиеся строки */
    .expandable-row { cursor: pointer; }
    .expandable-row:hover { background: #f0f4ff; }
    .toggle-icon { color: #999; font-size: 10px; margin-left: 8px; }
    .detail-cell { padding: 0 !important; background: #f8f9fa; }
    .detail-item {
        display: flex;
        justify-content: space-between;
        padding: 10px 20px;
        border-bottom: 1px solid #eee;
        color: #666;
        font-size: 13px;
    }
    .detail-item:last-child { border-bottom: none; }
    .detail-item.empty { justify-content: center; color: #999; }

    .error {
        background: #fff5f5;
        border: 1px solid #fed7d7;
        color: #dc3545;
        padding: 15px;
        border-radius: 8px;
        margin-bottom: 20px;
    }
    .success {
        background: #f0fff4;
        border: 1px solid #c6f6d5;
        color: #28a745;
        padding: 15px;
        border-radius: 8px;
        margin-bottom: 20px;
    }

    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    @media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } }

    .footer { text-align: center; color: #999; margin-top: 40px; font-size: 12px; }

    .btn {
        padding: 10px 20px;
        background: #0d6efd;
        border: none;
        border-radius: 8px;
        color: #fff;
        cursor: pointer;
        font-weight: 600;
        transition: background 0.2s;
    }
    .btn:hover { background: #0b5ed7; }
    .btn-small {
        padding: 4px 10px;
        font-size: 12px;
        text-decoration: none;
        color: #666;
        border: 1px solid #ddd;
        border-radius: 6px;
        margin-right: 4px;
        transition: all 0.2s;
    }
    .btn-small:hover { color: #0d6efd; border-color: #0d6efd; }
    .btn-danger { color: #dc3545 !important; border-color: #f5c2c7 !important; }
    .btn-danger:hover { background: #fff5f5; }
</style>
"""


# === LOGIN HTML ===
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin — Вход</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .card {
            background: #fff;
            padding: 40px;
            border-radius: 16px;
            width: 100%;
            max-width: 360px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.15);
        }
        h1 { color: #333; font-size: 24px; margin-bottom: 30px; text-align: center; font-weight: 600; }
        input {
            width: 100%;
            padding: 14px;
            background: #f5f7fa;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            color: #333;
            font-size: 16px;
            margin-bottom: 20px;
            transition: border-color 0.2s;
        }
        input:focus { outline: none; border-color: #667eea; }
        button {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            border-radius: 8px;
            color: #fff;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        button:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4); }
        <!-- ERROR -->
    </style>
</head>
<body>
    <div class="card">
        <h1>🔐 Bots Admin</h1>
        <form method="post">
            <input type="password" name="password" placeholder="Пароль" autofocus>
            <button type="submit">Войти</button>
        </form>
    </div>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
