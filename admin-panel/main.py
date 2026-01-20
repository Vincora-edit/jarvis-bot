"""
Admin Panel для Jarvis Bot
"""
import os
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

import aiosqlite
import aiohttp
import ssl

load_dotenv()

# Marzban API конфигурация
MARZBAN_URL = "https://72.56.88.242:8000"
MARZBAN_USERNAME = "Nfjk3khj43h043gj3\u201343"  # Unicode en-dash in username
MARZBAN_PASSWORD = "Vincorafjk3n4-423"

app = FastAPI(title="Jarvis Admin Panel")
app.add_middleware(
    SessionMiddleware,
    secret_key="jarvis-admin-secret-key-2026",
    session_cookie="admin_session",
    max_age=86400 * 7,  # 7 дней
)

# Конфигурация
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "JarvisAdmin2025")
JARVIS_DB_PATH = os.getenv("JARVIS_DB_PATH", "/opt/jarvis-bot/bot_database.db")


# === AUTH ===

def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    html = LOGIN_HTML
    if error:
        html = html.replace("<!-- ERROR -->", '<p style="color: #dc3545; text-align: center; margin-bottom: 15px;">Неверный пароль</p>')
    return HTMLResponse(html)


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["user"] = "admin"
        return RedirectResponse(url="/", status_code=303)
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
            <td>{r['username']}</td>
            <td><code>{r['referral_code'] or '-'}</code></td>
            <td>{r['referral_count']}</td>
            <td>{r['bonus_days']} дн.</td>
        </tr>
        """

    # Последние рефералы
    recent_rows = ""
    for r in data.get("recent_referrals", []):
        recent_rows += f"""
        <tr>
            <td>{r['created_at']}</td>
            <td>{r['username']}</td>
            <td>{r['referrer']}</td>
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
                <a href="/">Статистика</a>
                <a href="/vpn">VPN</a>
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
            <td><code>{p['code']}</code></td>
            <td>{type_label}</td>
            <td>{value_text}</td>
            <td>{p['description']}</td>
            <td style="font-size:11px">{restrictions_text}</td>
            <td>{uses_text}</td>
            <td>
                <a href="/promo/toggle/{p['id']}" class="btn-small">{'Откл' if p['is_active'] else 'Вкл'}</a>
                <a href="/promo/delete/{p['id']}" class="btn-small btn-danger" onclick="return confirm('Удалить?')">✕</a>
            </td>
        </tr>
        """

    # Таблица использований
    usages_rows = ""
    for u in data.get("usages", []):
        tg_id = u.get('telegram_id', '')
        tg_link = f'<a href="tg://user?id={tg_id}" style="color:#0d6efd">{u["username"]}</a>' if tg_id else u["username"]
        usages_rows += f"""
        <tr>
            <td>{u['used_at']}</td>
            <td><code>{u['code']}</code></td>
            <td>{tg_link}</td>
            <td><code>{tg_id}</code></td>
            <td>
                <a href="/promo/reset-usage/{u['id']}" class="btn-small btn-danger"
                   onclick="return confirm('Сбросить промокод для {u['username']}?\\n\\nЭто удалит использование промокода, подписку и VPN ключи пользователя.')">
                   🔄 Сбросить
                </a>
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
                <a href="/">Статистика</a>
                <a href="/vpn">VPN</a>
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

        <div class="section">
            <h2>Последние использования</h2>
            <table>
                <thead>
                    <tr><th>Дата</th><th>Промокод</th><th>Пользователь</th><th>Telegram ID</th><th>Действия</th></tr>
                </thead>
                <tbody>
                    {usages_rows if usages_rows else "<tr><td colspan='5' class='empty'>Нет данных</td></tr>"}
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


# === VPN USERS (Marzban) ===

async def get_marzban_token():
    """Получить токен авторизации Marzban"""
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            f"{MARZBAN_URL}/api/admin/token",
            data={"username": MARZBAN_USERNAME, "password": MARZBAN_PASSWORD}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("access_token")
    return None


async def get_marzban_users():
    """Получить список пользователей из Marzban"""
    token = await get_marzban_token()
    if not token:
        return None, "Не удалось авторизоваться в Marzban"

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(
            f"{MARZBAN_URL}/api/users",
            headers={"Authorization": f"Bearer {token}"}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("users", []), None
            return None, f"Ошибка API: {resp.status}"


async def marzban_toggle_user(username: str, disable: bool):
    """Включить/отключить пользователя в Marzban"""
    token = await get_marzban_token()
    if not token:
        return False

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        status = "disabled" if disable else "active"
        async with session.put(
            f"{MARZBAN_URL}/api/user/{username}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"status": status}
        ) as resp:
            return resp.status == 200


async def marzban_delete_user(username: str):
    """Удалить пользователя из Marzban"""
    token = await get_marzban_token()
    if not token:
        return False

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.delete(
            f"{MARZBAN_URL}/api/user/{username}",
            headers={"Authorization": f"Bearer {token}"}
        ) as resp:
            return resp.status == 200


def bytes_to_human(size):
    """Конвертация байтов в читаемый формат"""
    if not size or size == 0:
        return "0"
    for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ПБ"


@app.get("/vpn", response_class=HTMLResponse)
async def vpn_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    users, error = await get_marzban_users()
    html = render_vpn_page(users, error)
    return HTMLResponse(html)


@app.get("/vpn/toggle/{username}")
async def vpn_toggle(request: Request, username: str):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Получаем текущий статус
    users, _ = await get_marzban_users()
    if users:
        for u in users:
            if u.get("username") == username:
                is_active = u.get("status") == "active"
                await marzban_toggle_user(username, disable=is_active)
                break

    return RedirectResponse(url="/vpn", status_code=302)


@app.get("/vpn/delete/{username}")
async def vpn_delete(request: Request, username: str):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await marzban_delete_user(username)
    return RedirectResponse(url="/vpn", status_code=302)


def render_vpn_page(users: list, error: str) -> str:
    """Рендер страницы VPN пользователей"""

    # Статистика
    total_users = len(users) if users else 0
    active_users = sum(1 for u in users if u.get("status") == "active") if users else 0
    total_traffic = sum(u.get("used_traffic", 0) for u in users) if users else 0

    # Таблица пользователей
    users_rows = ""
    if users:
        for u in users:
            username = u.get("username", "")
            status = u.get("status", "unknown")
            status_emoji = "🟢" if status == "active" else "🔴" if status == "disabled" else "⚪"

            used_traffic = bytes_to_human(u.get("used_traffic", 0))
            data_limit = u.get("data_limit")
            limit_text = bytes_to_human(data_limit) if data_limit else "∞"

            expire = u.get("expire")
            if expire:
                expire_date = datetime.fromtimestamp(expire)
                days_left = (expire_date - datetime.now()).days
                if days_left < 0:
                    expire_text = f"<span style='color:#dc3545'>Истёк</span>"
                elif days_left <= 3:
                    expire_text = f"<span style='color:#ffc107'>{expire_date.strftime('%d.%m.%Y')} ({days_left}д)</span>"
                else:
                    expire_text = f"{expire_date.strftime('%d.%m.%Y')} ({days_left}д)"
            else:
                expire_text = "♾️"

            # Telegram ID и имя из note
            note = u.get("note") or ""
            tg_id = ""
            tg_name = ""
            if note:
                if "ID:" in note:
                    try:
                        tg_id = note.split("ID:")[1].split(")")[0].strip()
                    except:
                        pass
                if "Telegram:" in note and "(" in note:
                    try:
                        tg_name = note.split("Telegram:")[1].split("(")[0].strip()
                    except:
                        pass

            if tg_id:
                display_text = tg_name if tg_name else tg_id
                tg_display = f'<a href="tg://user?id={tg_id}" style="color:#0d6efd" title="Открыть в Telegram">{display_text}</a>'
            else:
                tg_display = "-"

            online = u.get("online_at")
            if online:
                online_text = datetime.fromisoformat(online.replace("Z", "")).strftime("%d.%m %H:%M")
            else:
                online_text = "-"

            # ip_limit не поддерживается в Marzban API — всегда ∞
            devices_text = "∞"

            toggle_text = "Откл" if status == "active" else "Вкл"

            users_rows += f"""
            <tr>
                <td>{status_emoji}</td>
                <td><code>{username}</code></td>
                <td>{tg_display}</td>
                <td>{devices_text}</td>
                <td>{used_traffic} / {limit_text}</td>
                <td>{expire_text}</td>
                <td>{online_text}</td>
                <td>
                    <a href="/vpn/toggle/{username}" class="btn-small">{toggle_text}</a>
                    <a href="/vpn/delete/{username}" class="btn-small btn-danger" onclick="return confirm('Удалить пользователя {username}?')">✕</a>
                </td>
            </tr>
            """

    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VPN пользователи — Admin</title>
    {COMMON_STYLES}
</head>
<body>
    <div class="container">
        <header>
            <h1>🔐 VPN пользователи</h1>
            <nav>
                <a href="/">Статистика</a>
                <a href="/vpn" class="active">VPN</a>
                <a href="/promo">Промокоды</a>
                <a href="/referrals">Рефералы</a>
                <a href="/logout">Выйти</a>
            </nav>
        </header>

        {"<div class='error'>Ошибка: " + str(error) + "</div>" if error else ""}

        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-value">{total_users}</div>
                <div class="stat-label">Всего пользователей</div>
            </div>
            <div class="stat-card green">
                <div class="stat-value">{active_users}</div>
                <div class="stat-label">Активных</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">{bytes_to_human(total_traffic)}</div>
                <div class="stat-label">Общий трафик</div>
            </div>
        </div>

        <div class="section">
            <h2>Пользователи Marzban</h2>
            <table>
                <thead>
                    <tr>
                        <th>Статус</th>
                        <th>Username</th>
                        <th>Telegram</th>
                        <th>Устройств</th>
                        <th>Трафик</th>
                        <th>Истекает</th>
                        <th>Онлайн</th>
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    {users_rows if users_rows else "<tr><td colspan='8' class='empty'>Нет пользователей</td></tr>"}
                </tbody>
            </table>
        </div>

        <p class="footer">
            Данные из <a href="{MARZBAN_URL}" target="_blank" style="color:#6c757d">Marzban</a> •
            Обновлено: {datetime.now().strftime("%d.%m.%Y %H:%M")}
        </p>
    </div>
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

            # Пользователи
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
                    (SELECT COALESCE(SUM(estimated_cost_cents), 0) FROM api_usage_logs WHERE user_id = u.id) as cost_cents
                FROM users u
                ORDER BY requests DESC
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

                data["users"].append({
                    "id": row[0],
                    "telegram_id": row[1],
                    "username": display_name,
                    "first_name": row[3] or "-",
                    "calendar": "✅" if row[4] else "❌",
                    "created_at": row[5][:10] if row[5] else "-",
                    "last_activity": row[6][:16].replace("T", " ") if row[6] else "-",
                    "requests": row[7],
                    "tokens": row[8],
                    "cost": round(row[9] / 100, 2) if row[9] else 0,
                })

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
    """Рендер страницы Jarvis Bot"""
    s = data.get("summary", {})
    error = data.get("error")

    # Подсчёт итогов по пользователям
    users = data.get("users", [])
    total_requests = sum(u['requests'] for u in users)
    total_tokens = sum(u['tokens'] for u in users)
    total_cost = sum(u['cost'] for u in users)
    total_calendars = sum(1 for u in users if u['calendar'] == "✅")

    # Таблица пользователей
    users_rows = ""
    for i, u in enumerate(users, 1):
        users_rows += f"""
        <tr>
            <td>{i}</td>
            <td>{u['username']}</td>
            <td><code>{u['telegram_id']}</code></td>
            <td>{u['calendar']}</td>
            <td>{u['created_at']}</td>
            <td>{u['last_activity']}</td>
            <td>{u['requests']:,}</td>
            <td>{u['tokens']:,}</td>
            <td>${u['cost']:.2f}</td>
        </tr>
        """

    # Строка "Итого"
    users_rows += f"""
        <tr style="background: #f8f9fa; font-weight: 600; border-top: 2px solid #dee2e6;">
            <td colspan="3">Итого: {len(users)} пользователей</td>
            <td>{total_calendars} ✅</td>
            <td colspan="2"></td>
            <td>{total_requests:,}</td>
            <td>{total_tokens:,}</td>
            <td>${total_cost:.2f}</td>
        </tr>
        """

    # Таблица API
    api_rows = ""
    for a in data.get("api_by_type", []):
        api_rows += f"""
        <tr>
            <td>{a['type']}</td>
            <td><code>{a['model']}</code></td>
            <td>{a['requests']:,}</td>
            <td>{a['tokens']:,}</td>
            <td>${a['cost']:.2f}</td>
            <td>{a['avg_time']} ms</td>
        </tr>
        """

    # Таблица функций
    features_rows = ""
    for f in data.get("features", []):
        if f['name'] == 'Напоминания':
            continue
        features_rows += f"""
        <tr><td>{f['name']}</td><td>{f['count']:,}</td></tr>
        """

    # VPN пользователи
    vpn_users = data.get("vpn_users", 0)
    features_rows += f"""
        <tr><td>🔐 VPN пользователей</td><td>{vpn_users:,}</td></tr>
        """

    # Напоминания с детализацией
    reminders = data.get("reminders", {})
    reminders_total = reminders.get("total", 0)
    reminders_sent = reminders.get("sent", 0)
    reminders_pending = reminders.get("pending", 0)

    features_rows += f"""
        <tr class="expandable-row" onclick="toggleReminders()">
            <td>Напоминания <span class="toggle-icon" id="reminders-toggle">▼</span></td>
            <td>{reminders_total:,}</td>
        </tr>
        <tr id="reminders-detail" style="display: none;">
            <td colspan="2" class="detail-cell">
                <div class="detail-item"><span>✅ Отправлено</span><span>{reminders_sent}</span></div>
                <div class="detail-item"><span>⏳ Ожидает</span><span>{reminders_pending}</span></div>
            </td>
        </tr>
        """

    # Привычки с детализацией
    habits_detail = data.get("habits_detail", [])
    habits_total = data.get("habits_total", 0)
    habits_items = ""
    for h in habits_detail:
        habits_items += f'<div class="detail-item"><span>{h["emoji"]} {h["name"]}</span><span>{h["count"]}</span></div>'

    features_rows += f"""
        <tr class="expandable-row" onclick="toggleHabits()">
            <td>Привычки <span class="toggle-icon" id="habits-toggle">▼</span></td>
            <td>{habits_total:,}</td>
        </tr>
        <tr id="habits-detail" style="display: none;">
            <td colspan="2" class="detail-cell">
                {habits_items if habits_items else '<div class="detail-item empty">Нет данных</div>'}
            </td>
        </tr>
        """


    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jarvis Bot — Admin</title>
    {COMMON_STYLES}
</head>
<body>
    <div class="container">
        <header>
            <h1>🤖 Jarvis Bot</h1>
            <nav>
                <a href="/" class="active">Статистика</a>
                <a href="/vpn">VPN</a>
                <a href="/promo">Промокоды</a>
                <a href="/referrals">Рефералы</a>
                <a href="/logout">Выйти</a>
            </nav>
        </header>

        {"<div class='error'>Ошибка: " + str(error) + "</div>" if error else ""}

        <div class="section">
            <h2>Пользователи</h2>
            <table>
                <thead>
                    <tr>
                        <th>#</th><th>Username</th><th>Telegram ID</th><th>Календарь</th>
                        <th>Регистрация</th><th>Последняя активность</th>
                        <th>Запросы</th><th>Токены</th><th>Стоимость</th>
                    </tr>
                </thead>
                <tbody>
                    {users_rows if users_rows else "<tr><td colspan='9' class='empty'>Нет данных</td></tr>"}
                </tbody>
            </table>
        </div>

        <div class="grid-2">
            <div class="section">
                <h2>API по типам</h2>
                <table>
                    <thead>
                        <tr><th>Тип</th><th>Модель</th><th>Запросы</th><th>Токены</th><th>Стоимость</th><th>Время</th></tr>
                    </thead>
                    <tbody>
                        {api_rows if api_rows else "<tr><td colspan='6' class='empty'>Нет данных</td></tr>"}
                    </tbody>
                </table>
            </div>

            <div class="section">
                <h2>Использование функций</h2>
                <table>
                    <thead><tr><th>Функция</th><th>Использований</th></tr></thead>
                    <tbody>
                        {features_rows if features_rows else "<tr><td colspan='2' class='empty'>Нет данных</td></tr>"}
                    </tbody>
                </table>
            </div>
        </div>

        <p class="footer">Обновлено: {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
    </div>

    <script>
        function toggleHabits() {{
            const detail = document.getElementById('habits-detail');
            const toggle = document.getElementById('habits-toggle');
            if (detail.style.display === 'none') {{
                detail.style.display = 'table-row';
                toggle.textContent = '▲';
            }} else {{
                detail.style.display = 'none';
                toggle.textContent = '▼';
            }}
        }}
        function toggleReminders() {{
            const detail = document.getElementById('reminders-detail');
            const toggle = document.getElementById('reminders-toggle');
            if (detail.style.display === 'none') {{
                detail.style.display = 'table-row';
                toggle.textContent = '▲';
            }} else {{
                detail.style.display = 'none';
                toggle.textContent = '▼';
            }}
        }}
    </script>
</body>
</html>
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
