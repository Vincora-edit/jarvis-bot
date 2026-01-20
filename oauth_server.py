"""
Простой HTTP-сервер для обработки OAuth callback от Google.
Запускается параллельно с ботом.
"""
import asyncio
from aiohttp import web

from config import config
from services.google_oauth_service import GoogleOAuthService
from database import async_session
from services.memory_service import MemoryService
from create_bot import bot


async def oauth_callback(request: web.Request) -> web.Response:
    """Обработчик OAuth callback от Google"""
    code = request.query.get('code')
    state = request.query.get('state')
    error = request.query.get('error')

    if error:
        return web.Response(
            text=f"""
            <html>
            <head><meta charset="utf-8"><title>Ошибка</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1>❌ Ошибка авторизации</h1>
                <p>{error}</p>
                <p>Закрой это окно и попробуй снова в боте.</p>
            </body>
            </html>
            """,
            content_type='text/html'
        )

    if not code or not state:
        return web.Response(
            text="""
            <html>
            <head><meta charset="utf-8"><title>Ошибка</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1>❌ Неверный запрос</h1>
                <p>Отсутствует код авторизации.</p>
            </body>
            </html>
            """,
            content_type='text/html'
        )

    # Обмениваем код на токены
    oauth = GoogleOAuthService()
    creds_dict, telegram_id = oauth.exchange_code(code, state)

    if not creds_dict or not telegram_id:
        return web.Response(
            text="""
            <html>
            <head><meta charset="utf-8"><title>Ошибка</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1>❌ Ошибка</h1>
                <p>Не удалось получить токены. Сессия истекла.</p>
                <p>Попробуй снова: /connect_calendar</p>
            </body>
            </html>
            """,
            content_type='text/html'
        )

    # Сохраняем токены в БД
    async with async_session() as session:
        memory = MemoryService(session)
        user, _ = await memory.get_or_create_user(telegram_id)

        user.google_credentials = creds_dict
        user.calendar_connected = True
        await session.commit()

    # Уведомляем пользователя в Telegram
    try:
        await bot.send_message(
            telegram_id,
            "✅ **Календарь подключён!**\n\n"
            "Теперь ты видишь свой личный Google Calendar.\n"
            "Попробуй: /today",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Error sending notification: {e}")

    return web.Response(
        text="""
        <html>
        <head>
            <meta charset="utf-8">
            <title>Успешно!</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                    text-align: center;
                    padding: 50px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    margin: 0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .card {
                    background: white;
                    padding: 40px 60px;
                    border-radius: 20px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                }
                h1 { color: #27ae60; margin-bottom: 10px; }
                p { color: #666; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>✅ Готово!</h1>
                <p>Календарь успешно подключён.</p>
                <p>Можешь закрыть это окно и вернуться в Telegram.</p>
            </div>
        </body>
        </html>
        """,
        content_type='text/html'
    )


async def health_check(request: web.Request) -> web.Response:
    """Проверка работоспособности"""
    return web.Response(text="OK")


async def privacy_policy(request: web.Request) -> web.Response:
    """Страница политики конфиденциальности"""
    return web.Response(
        text="""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Политика конфиденциальности — Джарвис</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 20px;
        }
        .card {
            background: white;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }
        h1 {
            color: #667eea;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .subtitle {
            color: #888;
            margin-bottom: 30px;
            font-size: 14px;
        }
        h2 {
            color: #444;
            margin: 25px 0 15px;
            font-size: 18px;
        }
        p, li {
            color: #555;
            margin-bottom: 12px;
        }
        ul {
            margin-left: 20px;
            margin-bottom: 15px;
        }
        .highlight {
            background: #f0f4ff;
            border-left: 4px solid #667eea;
            padding: 15px;
            margin: 20px 0;
            border-radius: 0 8px 8px 0;
        }
        .contact {
            background: #f9f9f9;
            padding: 20px;
            border-radius: 8px;
            margin-top: 30px;
        }
        a { color: #667eea; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>Политика конфиденциальности</h1>
            <p class="subtitle">Последнее обновление: 26 декабря 2025 г.</p>

            <p>Настоящая Политика конфиденциальности описывает, как Telegram-бот «Джарвис» (@Core_focus_bot) собирает, использует и защищает ваши данные.</p>

            <h2>1. Какие данные мы собираем</h2>
            <p>При использовании бота мы можем собирать:</p>
            <ul>
                <li><strong>Данные Telegram:</strong> ваш Telegram ID, имя пользователя, имя</li>
                <li><strong>Данные Google Calendar:</strong> при подключении календаря — доступ к просмотру и созданию событий</li>
                <li><strong>Сообщения:</strong> текст сообщений, которые вы отправляете боту для обработки</li>
                <li><strong>Данные привычек:</strong> информация о ваших привычках и их выполнении</li>
            </ul>

            <h2>2. Как мы используем данные</h2>
            <p>Собранные данные используются исключительно для:</p>
            <ul>
                <li>Предоставления функций бота (планирование, напоминания, работа с календарём)</li>
                <li>Персонализации взаимодействия</li>
                <li>Улучшения качества сервиса</li>
            </ul>

            <div class="highlight">
                <strong>Важно:</strong> Мы не продаём, не передаём и не раскрываем ваши данные третьим лицам, за исключением случаев, предусмотренных законом.
            </div>

            <h2>3. Google Calendar API</h2>
            <p>При подключении Google Calendar:</p>
            <ul>
                <li>Мы запрашиваем доступ только к календарю (чтение и создание событий)</li>
                <li>Токены доступа хранятся в зашифрованном виде</li>
                <li>Вы можете отключить доступ в любой момент через настройки Google аккаунта</li>
            </ul>

            <h2>4. Безопасность данных</h2>
            <p>Мы применяем следующие меры защиты:</p>
            <ul>
                <li>Шифрование данных при хранении (AES-256)</li>
                <li>Безопасная передача данных (HTTPS)</li>
                <li>Ограниченный доступ к серверам</li>
            </ul>

            <h2>5. Хранение данных</h2>
            <p>Данные хранятся на защищённых серверах. Вы можете запросить удаление всех ваших данных, написав команду /delete_my_data в боте или связавшись с нами.</p>

            <h2>6. Ваши права</h2>
            <p>Вы имеете право:</p>
            <ul>
                <li>Запросить копию ваших данных</li>
                <li>Запросить удаление данных</li>
                <li>Отозвать доступ к Google Calendar</li>
                <li>Прекратить использование бота в любой момент</li>
            </ul>

            <h2>7. Изменения политики</h2>
            <p>Мы можем обновлять данную политику. О существенных изменениях мы уведомим через бота.</p>

            <div class="contact">
                <h2>Контакты</h2>
                <p>По вопросам конфиденциальности:</p>
                <p>Telegram: <a href="https://t.me/Core_focus_bot">@Core_focus_bot</a></p>
            </div>
        </div>
    </div>
</body>
</html>
        """,
        content_type='text/html'
    )


def create_app() -> web.Application:
    """Создать веб-приложение"""
    app = web.Application()
    app.router.add_get('/oauth/callback', oauth_callback)
    app.router.add_get('/health', health_check)
    app.router.add_get('/privacy', privacy_policy)
    return app


async def run_oauth_server(port: int = 8080):
    """Запустить OAuth сервер"""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 OAuth сервер запущен на порту {port}")
    return runner


if __name__ == "__main__":
    # Для отдельного запуска сервера
    async def main():
        runner = await run_oauth_server()
        try:
            await asyncio.Event().wait()  # Бесконечно
        finally:
            await runner.cleanup()

    asyncio.run(main())
