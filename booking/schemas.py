"""
Pydantic схемы для валидации данных бронирования.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class BookingCreate(BaseModel):
    """Данные для создания бронирования"""
    slot_datetime: str  # ISO формат: 2025-01-20T10:00:00
    guest_name: str
    guest_email: EmailStr
    guest_notes: Optional[str] = None

    @field_validator("guest_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Имя должно содержать минимум 2 символа")
        if len(v) > 100:
            raise ValueError("Имя слишком длинное")
        return v

    @field_validator("guest_notes")
    @classmethod
    def notes_length(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > 500:
            raise ValueError("Комментарий слишком длинный (макс. 500 символов)")
        return v


class BookingLinkCreate(BaseModel):
    """Данные для создания ссылки бронирования"""
    title: str
    duration_minutes: int = 30
    description: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Название должно содержать минимум 2 символа")
        if len(v) > 100:
            raise ValueError("Название слишком длинное")
        return v

    @field_validator("duration_minutes")
    @classmethod
    def duration_valid(cls, v: int) -> int:
        if v not in [15, 30, 45, 60, 90, 120]:
            raise ValueError("Длительность должна быть 15, 30, 45, 60, 90 или 120 минут")
        return v


class SlotResponse(BaseModel):
    """Слот для бронирования"""
    time: str  # "10:00"
    datetime_iso: str  # ISO формат для передачи в форму


class BookingResponse(BaseModel):
    """Информация о бронировании"""
    id: int
    guest_name: str
    guest_email: str
    start_time: datetime
    end_time: datetime
    status: str
    cancel_url: Optional[str] = None


class BookingLinkResponse(BaseModel):
    """Информация о ссылке бронирования"""
    id: int
    slug: str
    title: str
    duration_minutes: int
    description: Optional[str]
    is_active: bool
    url: str
