"""
FastAPI роутер для сервиса бронирования.
"""
import json
import logging
from datetime import datetime, date
from typing import Optional

import pytz
from fastapi import FastAPI, HTTPException, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import EmailStr, ValidationError

from database.connection import async_session
from database.models import User
from booking.service import BookingService
from booking.schemas import BookingCreate
from config import config
from sqlalchemy import select

logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Jarvis Booking",
    description="Сервис бронирования встреч",
    version="1.0.0",
)

# Шаблоны
templates = Jinja2Templates(directory="booking/templates")

# Статика
app.mount("/static", StaticFiles(directory="static"), name="static")

# Таймзона
tz = pytz.timezone(config.TIMEZONE)


# === ПУБЛИЧНЫЕ СТРАНИЦЫ ===

@app.get("/book/{slug}", response_class=HTMLResponse)
async def booking_page(request: Request, slug: str):
    """Главная страница бронирования"""
    async with async_session() as session:
        service = BookingService(session)
        link = await service.get_booking_link_by_slug(slug)

        if not link or not link.is_active:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Ссылка не найдена или неактивна"},
                status_code=404,
            )

        # Получаем доступные даты
        available_dates = await service.get_available_dates(link)

        # Преобразуем даты в список ISO-строк для JS
        available_dates_list = [d["date"].isoformat() for d in available_dates]

        return templates.TemplateResponse(
            "calendar.html",
            {
                "request": request,
                "link": link,
                "dates": available_dates,
                "available_dates_json": json.dumps(available_dates_list),
                "user_name": link.user.first_name or link.user.username or "Пользователь",
            },
        )


@app.get("/api/slots/{slug}")
async def get_slots(slug: str, date: str):
    """API: Получить слоты на дату"""
    async with async_session() as session:
        service = BookingService(session)
        link = await service.get_booking_link_by_slug(slug)

        if not link or not link.is_active:
            raise HTTPException(status_code=404, detail="Ссылка не найдена")

        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Неверный формат даты")

        slots = await service.get_available_slots(link, target_date)

        return {
            "date": date,
            "slots": [
                {
                    "time": s["time"],
                    "datetime_iso": s["datetime"].isoformat(),
                }
                for s in slots
            ],
        }


@app.get("/api/slots-html/{slug}", response_class=HTMLResponse)
async def get_slots_html(request: Request, slug: str, date: str):
    """HTMX: Получить HTML со слотами"""
    async with async_session() as session:
        service = BookingService(session)
        link = await service.get_booking_link_by_slug(slug)

        if not link or not link.is_active:
            return HTMLResponse("<p>Ссылка не найдена</p>", status_code=404)

        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return HTMLResponse("<p>Неверный формат даты</p>", status_code=400)

        slots = await service.get_available_slots(link, target_date)

        return templates.TemplateResponse(
            "partials/slots.html",
            {
                "request": request,
                "slots": slots,
                "date": date,
                "slug": slug,
            },
        )


@app.post("/book/{slug}", response_class=HTMLResponse)
async def create_booking(
    request: Request,
    slug: str,
    slot_datetime: str = Form(...),
    guest_name: str = Form(...),
    guest_email: str = Form(...),
    guest_notes: str = Form(None),
):
    """Создать бронирование"""
    async with async_session() as session:
        service = BookingService(session)
        link = await service.get_booking_link_by_slug(slug)

        if not link or not link.is_active:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Ссылка не найдена"},
                status_code=404,
            )

        # Валидация
        errors = []
        guest_name = guest_name.strip()
        guest_email = guest_email.strip().lower()

        if len(guest_name) < 2:
            errors.append("Введите имя (минимум 2 символа)")

        if "@" not in guest_email or "." not in guest_email:
            errors.append("Введите корректный email")

        try:
            start_time = datetime.fromisoformat(slot_datetime)
            # Убеждаемся что время в нужной таймзоне
            if start_time.tzinfo is None:
                start_time = tz.localize(start_time)
        except ValueError:
            errors.append("Неверный формат времени")
            start_time = None

        if errors:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": " | ".join(errors)},
                status_code=400,
            )

        # Проверяем, что слот ещё свободен
        target_date = start_time.date()
        available_slots = await service.get_available_slots(link, target_date)

        # Сравниваем по времени (HH:MM) — самый надёжный способ
        start_time_str = start_time.strftime("%H:%M")
        slot_available = any(
            s["time"] == start_time_str
            for s in available_slots
        )

        if not slot_available:
            logger.warning(f"Слот {start_time_str} недоступен. Доступные: {[s['time'] for s in available_slots]}")
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Этот слот уже занят. Выберите другое время."},
                status_code=400,
            )

        # Создаём бронирование
        booking = await service.create_booking(
            booking_link=link,
            start_time=start_time,
            guest_name=guest_name,
            guest_email=guest_email,
            guest_notes=guest_notes,
        )

        # Отправляем уведомление владельцу
        await notify_booking_owner(link.user.telegram_id, booking, link)

        # Страница подтверждения
        return templates.TemplateResponse(
            "confirmation.html",
            {
                "request": request,
                "booking": booking,
                "link": link,
                "cancel_url": f"/booking/cancel/{booking.cancel_token}",
            },
        )


@app.get("/booking/cancel/{token}", response_class=HTMLResponse)
async def cancel_booking_page(request: Request, token: str):
    """Страница отмены бронирования"""
    async with async_session() as session:
        service = BookingService(session)
        booking = await service.get_booking_by_cancel_token(token)

        if not booking:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Бронирование не найдено"},
                status_code=404,
            )

        if booking.status == "cancelled":
            return templates.TemplateResponse(
                "cancelled.html",
                {"request": request, "booking": booking, "already_cancelled": True},
            )

        return templates.TemplateResponse(
            "cancel_confirm.html",
            {"request": request, "booking": booking, "token": token},
        )


@app.post("/booking/cancel/{token}", response_class=HTMLResponse)
async def cancel_booking(request: Request, token: str):
    """Отменить бронирование"""
    async with async_session() as session:
        service = BookingService(session)
        booking = await service.get_booking_by_cancel_token(token)

        if not booking:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Бронирование не найдено"},
                status_code=404,
            )

        if booking.status == "cancelled":
            return templates.TemplateResponse(
                "cancelled.html",
                {"request": request, "booking": booking, "already_cancelled": True},
            )

        # Отменяем
        user_telegram_id = booking.booking_link.user.telegram_id
        await service.cancel_booking(booking)

        # Уведомляем владельца
        await notify_booking_cancelled(user_telegram_id, booking)

        return templates.TemplateResponse(
            "cancelled.html",
            {"request": request, "booking": booking, "already_cancelled": False},
        )


# === УВЕДОМЛЕНИЯ ===

def _escape_md(text: str) -> str:
    """Экранировать спецсимволы Markdown"""
    if not text:
        return ""
    # Экранируем символы, которые ломают Markdown парсер
    for char in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(char, '\\' + char)
    return text


async def notify_booking_owner(telegram_id: int, booking, link):
    """Отправить уведомление о новом бронировании"""
    try:
        from create_bot import bot

        start_local = booking.start_time
        if start_local.tzinfo is None:
            start_local = tz.localize(start_local)
        else:
            start_local = start_local.astimezone(tz)

        # Экранируем пользовательский ввод
        guest_name = _escape_md(booking.guest_name)
        guest_email = _escape_md(booking.guest_email)
        title = _escape_md(link.title)

        text = (
            f"📅 *Новое бронирование\\!*\n\n"
            f"*{title}*\n"
            f"Гость: {guest_name}\n"
            f"Email: {guest_email}\n"
            f"Дата: {start_local.strftime('%d.%m.%Y')}\n"
            f"Время: {start_local.strftime('%H:%M')} \\({link.duration_minutes} мин\\)"
        )

        if booking.guest_notes:
            text += f"\n\nКомментарий: {_escape_md(booking.guest_notes)}"

        await bot.send_message(telegram_id, text, parse_mode="MarkdownV2")
        logger.info(f"Уведомление о бронировании отправлено user {telegram_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")


async def notify_booking_cancelled(telegram_id: int, booking):
    """Отправить уведомление об отмене"""
    try:
        from create_bot import bot

        start_local = booking.start_time
        if start_local.tzinfo is None:
            start_local = tz.localize(start_local)
        else:
            start_local = start_local.astimezone(tz)

        guest_name = _escape_md(booking.guest_name)

        text = (
            f"❌ *Бронирование отменено*\n\n"
            f"Гость {guest_name} отменил встречу\n"
            f"Дата: {start_local.strftime('%d.%m.%Y %H:%M')}"
        )

        await bot.send_message(telegram_id, text, parse_mode="MarkdownV2")
        logger.info(f"Уведомление об отмене отправлено user {telegram_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об отмене: {e}")


# === HEALTH CHECK ===

@app.get("/health")
async def health_check():
    """Проверка работоспособности"""
    return {"status": "ok", "service": "booking"}
