from datetime import date, time, datetime
from typing import Optional
from pydantic import BaseModel

from app.models import BookingStatus


class ServiceOut(BaseModel):
    id: int
    category: str
    name: str
    price: int
    duration: int
    description: Optional[str] = None
    is_active: bool

    model_config = {"from_attributes": True}


class MasterOut(BaseModel):
    id: int
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


class SlotOut(BaseModel):
    time: str


class BookingCreate(BaseModel):
    telegram_user_id: int
    service_id: int
    master_id: Optional[int] = None
    desired_date: date
    desired_time: time
    name: str
    phone: str
    comment: Optional[str] = None


class BookingReschedule(BaseModel):
    desired_date: date
    desired_time: time


class ReviewCreate(BaseModel):
    rating: int
    comment: Optional[str] = None


class ClientOut(BaseModel):
    id: int
    name: str
    phone: str

    model_config = {"from_attributes": True}


class BookingOut(BaseModel):
    id: int
    client: ClientOut
    service: ServiceOut
    master: Optional[MasterOut] = None
    desired_date: date
    desired_time: time
    comment: Optional[str] = None
    status: BookingStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class SettingsOut(BaseModel):
    salon_name: str
    salon_phone: str
    salon_address: str
    salon_contacts: str
