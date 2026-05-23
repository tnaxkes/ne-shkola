from datetime import date, time, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus, Client, MasterService, Service, Master
from app.services.slots import get_available_slots
from app.timeutils import now_local


class BookingError(Exception):
    pass


def get_or_create_client(
    db: Session,
    telegram_user_id: int,
    name: str,
    phone: str,
) -> Client:
    client = db.query(Client).filter(Client.telegram_user_id == telegram_user_id).first()
    if not client:
        client = db.query(Client).filter(Client.phone == phone).first()
    if client:
        client.telegram_user_id = telegram_user_id
        client.name = name
        client.phone = phone
        client.last_seen_at = datetime.utcnow()
        client.visits_count += 1
    else:
        client = Client(
            telegram_user_id=telegram_user_id,
            name=name,
            phone=phone,
        )
        db.add(client)
    db.flush()
    return client


def pick_available_master_id(
    db: Session,
    service_id: int,
    desired_date: date,
    desired_time: time,
) -> Optional[int]:
    masters_ids = [
        ms.master_id
        for ms in db.query(MasterService).filter(MasterService.service_id == service_id).all()
    ]
    for mid in masters_ids:
        master = db.query(Master).filter(Master.id == mid, Master.is_active == True).first()
        if not master:
            continue
        slots = get_available_slots(db, service_id, desired_date, mid)
        if desired_time.strftime("%H:%M") in slots:
            return mid
    return None


def check_slot_available(
    db: Session,
    service_id: int,
    desired_date: date,
    desired_time: time,
    master_id: Optional[int],
) -> bool:
    slots = get_available_slots(db, service_id, desired_date, master_id)
    return desired_time.strftime("%H:%M") in slots


def create_booking(
    db: Session,
    telegram_user_id: int,
    service_id: int,
    desired_date: date,
    desired_time: time,
    name: str,
    phone: str,
    comment: Optional[str] = None,
    master_id: Optional[int] = None,
) -> Booking:
    now = now_local()
    booking_dt = datetime.combine(desired_date, desired_time)
    if booking_dt <= now.replace(tzinfo=None):
        raise BookingError("Дата и время должны быть в будущем.")

    service = db.query(Service).filter(Service.id == service_id, Service.is_active == True).first()
    if not service:
        raise BookingError("Программа не найдена или неактивна.")

    if master_id:
        ms = db.query(MasterService).filter(
            MasterService.master_id == master_id,
            MasterService.service_id == service_id,
        ).first()
        if not ms:
            raise BookingError("Преподаватель не ведёт выбранную программу.")
    else:
        master_id = pick_available_master_id(db, service_id, desired_date, desired_time)

    if not check_slot_available(db, service_id, desired_date, desired_time, master_id):
        raise BookingError("Выбранное время уже занято. Пожалуйста, выберите другое.")

    client = get_or_create_client(db, telegram_user_id, name, phone)

    booking = Booking(
        client_id=client.id,
        service_id=service_id,
        master_id=master_id,
        desired_date=desired_date,
        desired_time=desired_time,
        comment=comment if comment and comment != "-" else None,
        status=BookingStatus.new,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    try:
        from app.services.calendar import create_event
        event_id = create_event(booking)
        if event_id:
            booking.calendar_event_id = event_id
            db.commit()
    except Exception:
        pass

    return booking


def reschedule_booking(
    db: Session,
    booking_id: int,
    telegram_user_id: int,
    desired_date: date,
    desired_time: time,
) -> Booking:
    booking = (
        db.query(Booking)
        .join(Client)
        .filter(
            Booking.id == booking_id,
            Client.telegram_user_id == telegram_user_id,
            Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
        )
        .first()
    )
    if not booking:
        raise BookingError("Запись не найдена.")

    now = now_local()
    if datetime.combine(desired_date, desired_time) <= now.replace(tzinfo=None):
        raise BookingError("Новая дата должна быть в будущем.")

    if not check_slot_available(db, booking.service_id, desired_date, desired_time, booking.master_id):
        raise BookingError("Выбранное время занято.")

    booking.desired_date = desired_date
    booking.desired_time = desired_time
    booking.status = BookingStatus.new
    db.commit()
    db.refresh(booking)

    try:
        from app.services.calendar import update_event
        update_event(booking)
    except Exception:
        pass

    return booking


def cancel_booking(db: Session, booking_id: int, telegram_user_id: int) -> Booking:
    booking = (
        db.query(Booking)
        .join(Client)
        .filter(
            Booking.id == booking_id,
            Client.telegram_user_id == telegram_user_id,
            Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
        )
        .first()
    )
    if not booking:
        raise BookingError("Запись не найдена.")

    booking.status = BookingStatus.cancelled
    db.commit()
    db.refresh(booking)

    try:
        from app.services.calendar import update_event
        update_event(booking)
    except Exception:
        pass

    return booking
