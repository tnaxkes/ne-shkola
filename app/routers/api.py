import asyncio
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Booking, BookingReview, BookingStatus, Client, Master, MasterService, Service
from app.schemas import (
    BookingCreate, BookingOut, BookingReschedule, MasterOut,
    ReviewCreate, ServiceOut, SettingsOut, SlotOut,
)
from app.services.booking_service import BookingError, cancel_booking, create_booking, reschedule_booking
from app.services.slots import get_available_slots

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/services", response_model=list[ServiceOut])
def list_services(db: Session = Depends(get_db)):
    return db.query(Service).filter(Service.is_active == True).order_by(Service.category, Service.id).all()


@router.get("/masters", response_model=list[MasterOut])
def list_masters(service_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Master).filter(Master.is_active == True)
    if service_id:
        master_ids = [
            ms.master_id
            for ms in db.query(MasterService).filter(MasterService.service_id == service_id).all()
        ]
        q = q.filter(Master.id.in_(master_ids))
    return q.order_by(Master.id).all()


@router.get("/slots")
def list_slots(
    service_id: int,
    desired_date: date,
    master_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    slots = get_available_slots(db, service_id, desired_date, master_id)
    return {"slots": slots}


@router.get("/settings", response_model=SettingsOut)
def get_settings_public():
    return SettingsOut(
        salon_name=settings.salon_name,
        salon_phone=settings.salon_phone,
        salon_address=settings.salon_address,
        salon_contacts=settings.salon_contacts,
    )


@router.get("/bookings/history")
def booking_history(telegram_user_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.telegram_user_id == telegram_user_id).first()
    if not client:
        return {"bookings": []}
    bookings = (
        db.query(Booking)
        .filter(Booking.client_id == client.id)
        .order_by(Booking.desired_date.desc(), Booking.desired_time.desc())
        .limit(10)
        .all()
    )
    result = []
    for b in bookings:
        result.append({
            "id": b.id,
            "service_name": b.service.name,
            "master_name": b.master.name if b.master else None,
            "service_id": b.service_id,
            "desired_date": b.desired_date.isoformat(),
            "desired_time": b.desired_time.strftime("%H:%M"),
            "status": b.status.value,
            "comment": b.comment,
        })
    return {"bookings": result}


@router.get("/bookings/me")
def booking_me(telegram_user_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.telegram_user_id == telegram_user_id).first()
    if not client:
        return {"booking": None, "last_booking": None}

    active = (
        db.query(Booking)
        .filter(
            Booking.client_id == client.id,
            Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
        )
        .order_by(Booking.desired_date, Booking.desired_time)
        .first()
    )

    last = (
        db.query(Booking)
        .filter(Booking.client_id == client.id)
        .order_by(Booking.desired_date.desc(), Booking.desired_time.desc())
        .first()
    )

    def serialize(b):
        if not b:
            return None
        return {
            "id": b.id,
            "service_name": b.service.name,
            "service_id": b.service_id,
            "master_name": b.master.name if b.master else None,
            "desired_date": b.desired_date.isoformat(),
            "desired_time": b.desired_time.strftime("%H:%M"),
            "status": b.status.value,
            "comment": b.comment,
        }

    return {"booking": serialize(active), "last_booking": serialize(last)}


@router.post("/bookings")
async def create_booking_endpoint(payload: BookingCreate, db: Session = Depends(get_db)):
    try:
        booking = create_booking(
            db=db,
            telegram_user_id=payload.telegram_user_id,
            service_id=payload.service_id,
            desired_date=payload.desired_date,
            desired_time=payload.desired_time,
            name=payload.name,
            phone=payload.phone,
            comment=payload.comment,
            master_id=payload.master_id,
        )
    except BookingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Capture all ORM data as plain values NOW (session closes after response)
    booking_id       = booking.id
    service_name     = booking.service.name
    master_name      = booking.master.name if booking.master else None
    desired_date_iso = booking.desired_date.isoformat()
    desired_time_str = booking.desired_time.strftime("%H:%M")
    status_val       = booking.status.value

    # Notification data (plain strings — safe to use after session closes)
    notif = {
        "client_name":  booking.client.name,
        "client_phone": booking.client.phone,
        "service_name": service_name,
        "master_name":  master_name or "Не важно",
        "desired_date": booking.desired_date,
        "desired_time": booking.desired_time,
        "comment":      booking.comment,
    }

    # Fire admin notification in background — doesn't block the response
    asyncio.create_task(_send_admin_notification(notif))

    return {
        "id":           booking_id,
        "service_name": service_name,
        "master_name":  master_name,
        "desired_date": desired_date_iso,
        "desired_time": desired_time_str,
        "status":       status_val,
    }


async def _send_admin_notification(data: dict) -> None:
    """Send admin Telegram notification from plain data dict (no ORM session needed)."""
    if not settings.admin_telegram_chat_id or not settings.telegram_bot_token:
        return
    try:
        from app.services.telegram import _send
        from app.timeutils import format_date_ru, format_time
        text = (
            f"<b>Новая заявка</b>\n"
            f"Клиент: {data['client_name']}\n"
            f"Телефон: {data['client_phone']}\n"
            f"Программа: {data['service_name']}\n"
            f"Преподаватель: {data['master_name']}\n"
            f"Дата: {format_date_ru(data['desired_date'])}\n"
            f"Время: {format_time(data['desired_time'])}\n"
            f"Комментарий: {data['comment'] or '-'}"
        )
        await _send(settings.admin_telegram_chat_id, text)
    except Exception:
        pass


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking_endpoint(
    booking_id: int,
    telegram_user_id: int,
    db: Session = Depends(get_db),
):
    try:
        booking = cancel_booking(db, booking_id, telegram_user_id)
    except BookingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": booking.status.value}


@router.post("/bookings/{booking_id}/reschedule")
def reschedule_booking_endpoint(
    booking_id: int,
    telegram_user_id: int,
    payload: BookingReschedule,
    db: Session = Depends(get_db),
):
    try:
        booking = reschedule_booking(
            db, booking_id, telegram_user_id,
            payload.desired_date, payload.desired_time,
        )
    except BookingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "id": booking.id,
        "desired_date": booking.desired_date.isoformat(),
        "desired_time": booking.desired_time.strftime("%H:%M"),
        "status": booking.status.value,
    }


@router.post("/bookings/{booking_id}/review")
def leave_review(
    booking_id: int,
    telegram_user_id: int,
    payload: ReviewCreate,
    db: Session = Depends(get_db),
):
    booking = (
        db.query(Booking)
        .join(Client)
        .filter(
            Booking.id == booking_id,
            Client.telegram_user_id == telegram_user_id,
            Booking.status == BookingStatus.completed,
        )
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена.")
    if booking.review:
        raise HTTPException(status_code=400, detail="Отзыв уже оставлен.")
    if not 1 <= payload.rating <= 5:
        raise HTTPException(status_code=400, detail="Оценка от 1 до 5.")

    review = BookingReview(
        booking_id=booking_id,
        rating=payload.rating,
        comment=payload.comment if payload.comment and payload.comment != "-" else None,
    )
    db.add(review)
    db.commit()
    return {"ok": True}
