from datetime import date, time, datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, BookingStatus, MasterService
from app.timeutils import parse_workday_time


def _generate_grid(step_minutes: int, start: time, end: time) -> List[time]:
    slots = []
    current = datetime.combine(date.today(), start)
    end_dt = datetime.combine(date.today(), end)
    while current < end_dt:
        slots.append(current.time())
        current += timedelta(minutes=step_minutes)
    return slots


def _bookings_overlap(slot_start: time, slot_end: time, booking_start: time, booking_end: time) -> bool:
    return slot_start < booking_end and slot_end > booking_start


def _master_free_slots(
    db: Session,
    master_id: int,
    desired_date: date,
    duration: int,
    step: int,
    grid: List[time],
) -> List[time]:
    existing = (
        db.query(Booking)
        .filter(
            Booking.master_id == master_id,
            Booking.desired_date == desired_date,
            Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
        )
        .all()
    )
    free = []
    for slot in grid:
        slot_end_dt = (datetime.combine(date.today(), slot) + timedelta(minutes=duration)).time()
        blocked = False
        for b in existing:
            booking_end_dt = (
                datetime.combine(date.today(), b.desired_time) + timedelta(minutes=b.service.duration)
            ).time()
            if _bookings_overlap(slot, slot_end_dt, b.desired_time, booking_end_dt):
                blocked = True
                break
        if not blocked:
            free.append(slot)
    return free


def get_available_slots(
    db: Session,
    service_id: int,
    desired_date: date,
    master_id: Optional[int] = None,
) -> List[str]:
    from app.models import Service

    service = db.query(Service).filter(Service.id == service_id, Service.is_active == True).first()
    if not service:
        return []

    step = settings.slot_step_minutes
    ws = parse_workday_time(settings.workday_start)
    we = parse_workday_time(settings.workday_end)
    grid = _generate_grid(step, ws, we)

    if master_id:
        free = _master_free_slots(db, master_id, desired_date, service.duration, step, grid)
        return [t.strftime("%H:%M") for t in free]

    # no master — union of free slots across all masters for this service
    masters_ids = [
        ms.master_id
        for ms in db.query(MasterService).filter(MasterService.service_id == service_id).all()
    ]

    if not masters_ids:
        return [t.strftime("%H:%M") for t in grid]

    free_set: set = set()
    for mid in masters_ids:
        from app.models import Master
        master = db.query(Master).filter(Master.id == mid, Master.is_active == True).first()
        if not master:
            continue
        free = _master_free_slots(db, mid, desired_date, service.duration, step, grid)
        free_set.update(free)

    sorted_slots = sorted(free_set)
    return [t.strftime("%H:%M") for t in sorted_slots]
