"""
Subscription API для VPN клиентов.

FastAPI приложение, которое отдаёт конфигурации клиентам.
Работает на своём домене (vpn.jarvis.bot) или как часть бота.
"""

import base64
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, JSONResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from database.models import TunnelKey, User, Subscription

from .config import get_config
from .key_generator import SubscriptionTokenGenerator, VLESSKeyGenerator

logger = logging.getLogger(__name__)

# FastAPI приложение для subscription
app = FastAPI(
    title="Jarvis VPN Subscription",
    docs_url=None,      # Отключаем документацию
    redoc_url=None,
    openapi_url=None,
)


def get_token_generator() -> SubscriptionTokenGenerator:
    """Получить генератор токенов"""
    config = get_config()
    return SubscriptionTokenGenerator(config.subscription_secret)


def get_key_generator() -> VLESSKeyGenerator:
    """Получить генератор ключей"""
    config = get_config()
    return VLESSKeyGenerator(config.subscription_secret)


@app.get("/sub/{token}")
async def get_subscription(token: str, request: Request):
    """
    Получить конфигурацию VPN по токену.

    Этот endpoint вызывается VPN клиентами (Happ, v2rayNG, etc).
    Возвращает список VLESS URL в base64.

    Args:
        token: Токен пользователя (содержит user_id и подпись)

    Returns:
        Base64-encoded список VLESS URLs (один на строку)
    """
    # Проверяем токен
    token_gen = get_token_generator()
    user_id = token_gen.verify_token(token)

    if user_id is None:
        logger.warning(f"VPN sub: невалидный токен от {request.client.host}")
        raise HTTPException(status_code=404, detail="Not found")

    try:
        async with async_session() as session:
            # Получаем пользователя
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                raise HTTPException(status_code=404, detail="Not found")

            # Проверяем активную подписку
            sub_result = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.status == "active"
                )
            )
            subscription = sub_result.scalar_one_or_none()

            # Проверяем триал
            has_access = False
            if subscription:
                if subscription.expires_at is None or subscription.expires_at > datetime.utcnow():
                    has_access = True
            elif user.vpn_trial_used and user.vpn_trial_expires:
                if user.vpn_trial_expires > datetime.utcnow():
                    has_access = True

            if not has_access:
                # Возвращаем пустой конфиг (подписка истекла)
                logger.info(f"VPN sub: подписка истекла для user_id={user_id}")
                return PlainTextResponse(
                    content=base64.b64encode(b"# Subscription expired\n").decode(),
                    media_type="text/plain"
                )

            # Получаем активные ключи пользователя
            keys_result = await session.execute(
                select(TunnelKey).where(
                    TunnelKey.user_id == user_id,
                    TunnelKey.is_active == True
                )
            )
            keys = list(keys_result.scalars().all())

            if not keys:
                # Нет ключей
                return PlainTextResponse(
                    content=base64.b64encode(b"# No keys\n").decode(),
                    media_type="text/plain"
                )

            # Собираем VLESS URLs
            vless_urls = []
            config = get_config()
            key_gen = get_key_generator()

            for tunnel_key in keys:
                # Проверяем тип ключа по marzban_username
                is_new_key = (
                    tunnel_key.marzban_username and
                    tunnel_key.marzban_username.startswith("jarvis_")
                )
                is_legacy_marzban = (
                    tunnel_key.marzban_username and
                    tunnel_key.marzban_username.startswith("tg_user_")
                )

                if is_legacy_marzban:
                    # Старый ключ от Marzban - пропускаем (сервер выключен)
                    vless_urls.append(f"# Legacy (Marzban): {tunnel_key.device_name}")
                    continue

                # Генерируем VLESS URL для новых ключей
                # Извлекаем device_id из marzban_username (формат: jarvis_123_d11)
                device_id = 1
                if tunnel_key.marzban_username and "_d" in tunnel_key.marzban_username:
                    try:
                        device_id = int(tunnel_key.marzban_username.split("_d")[-1])
                    except ValueError:
                        pass

                # Берём первый доступный сервер для генерации URL
                server = config.get_best_server()
                if server and server.reality_public_key:
                    key = key_gen.create_key(
                        user_id=user_id,
                        device_id=device_id,
                        server_host=server.host,
                        server_port=server.inbound_port,
                        public_key=server.reality_public_key,
                        short_id=server.reality_short_id,
                        server_name=server.reality_server_name,
                        server_id=server.id,
                        name=tunnel_key.device_name,
                    )
                    vless_urls.append(key.to_vless_url())

            # Формируем ответ (base64 encoded, один URL на строку)
            content = "\n".join(vless_urls)
            encoded = base64.b64encode(content.encode()).decode()

            logger.debug(f"VPN sub: отдал {len(vless_urls)} ключей для user_id={user_id}")

            return PlainTextResponse(
                content=encoded,
                media_type="text/plain",
                headers={
                    "Content-Disposition": "attachment; filename=jarvis-vpn.txt",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={int(subscription.expires_at.timestamp()) if subscription and subscription.expires_at else 0}",
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"VPN sub: ошибка для token: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    config = get_config()
    servers_status = {
        s.id: s.status.value
        for s in config.servers
    }
    return JSONResponse({
        "status": "ok",
        "servers": servers_status,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.get("/")
async def root():
    """Корневой endpoint — редирект или заглушка"""
    return PlainTextResponse("Jarvis VPN")


# === ИНТЕГРАЦИЯ С ОСНОВНЫМ БОТОМ ===

def get_subscription_app() -> FastAPI:
    """
    Получить FastAPI приложение для монтирования.

    Использование в main.py бота:
    ```python
    from vpn.subscription import get_subscription_app
    from fastapi import FastAPI

    main_app = FastAPI()
    main_app.mount("/vpn", get_subscription_app())
    ```
    """
    return app


async def generate_subscription_url(user_id: int) -> str:
    """
    Сгенерировать subscription URL для пользователя.

    Вызывается из tunnel.py при создании ключа.
    """
    config = get_config()
    if not config.subscription_domain:
        # Домен не настроен, возвращаем пустую строку
        return ""

    token_gen = SubscriptionTokenGenerator(config.subscription_secret)
    return token_gen.generate_subscription_url(user_id, config.subscription_domain)
