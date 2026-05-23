import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import ADMIN_SESSION_KEY, is_authenticated, login, logout
from app.config import settings
from app.database import get_db
from app.models import (
    Booking, BookingReview, BookingStatus, Broadcast, BroadcastStatus,
    Client, Master, MasterService, ReminderLog, Service,
)
from app.services.calendar import update_event
from app.services.telegram import notify_booking_completed, notify_booking_confirmed
from app.timeutils import format_date_ru, format_datetime_ru, format_time, today_local

router = APIRouter(prefix="/admin")

# Use absolute path so templates are found both locally and on Vercel.
# Python 3.14 + Jinja2 3.1.x cache key bug workaround: disable template cache.
_TEMPLATES_DIR = str(Path(__file__).parent.parent / "templates")
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    cache_size=0,
)
templates = Jinja2Templates(env=_jinja_env)

# ─── Jinja2 filters ──────────────────────────────────────────────────────────

STATUS_LABELS = {
    "new": "Новая",
    "confirmed": "Подтверждена",
    "cancelled": "Отменена",
    "completed": "Завершена",
}
STATUS_CLASSES = {
    "new": "warning",
    "confirmed": "success",
    "cancelled": "danger",
    "completed": "secondary",
}


def _date_ru(d):
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return format_date_ru(d)


def _datetime_ru(dt):
    return format_datetime_ru(dt)


def _status_label(s):
    if hasattr(s, "value"):
        s = s.value
    return STATUS_LABELS.get(s, s)


def _status_class(s):
    if hasattr(s, "value"):
        s = s.value
    return STATUS_CLASSES.get(s, "secondary")


templates.env.filters["date_ru"] = _date_ru
templates.env.filters["datetime_ru"] = _datetime_ru
templates.env.filters["booking_status_label"] = _status_label
templates.env.filters["booking_status_class"] = _status_class


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def check_auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)
    return None


# ─── Login ────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse(request, "admin/login.html", {"error": None})


@router.post("/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if login(request, username, password):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse(
        request, "admin/login.html", {"error": "Неверный логин или пароль"}, status_code=401
    )


@router.get("/logout")
def admin_logout(request: Request):
    logout(request)
    return RedirectResponse("/admin/login", status_code=302)


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir

    today = today_local()
    tomorrow = today + timedelta(days=1)

    count_new = db.query(Booking).filter(Booking.status == BookingStatus.new).count()
    count_today = db.query(Booking).filter(
        Booking.desired_date == today,
        Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
    ).count()
    count_tomorrow = db.query(Booking).filter(
        Booking.desired_date == tomorrow,
        Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
    ).count()
    count_active = db.query(Booking).filter(
        Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
    ).count()

    new_bookings = (
        db.query(Booking)
        .filter(Booking.status == BookingStatus.new, Booking.desired_date >= today)
        .order_by(Booking.desired_date, Booking.desired_time)
        .limit(8)
        .all()
    )
    upcoming = (
        db.query(Booking)
        .filter(
            Booking.status == BookingStatus.confirmed,
            Booking.desired_date >= today,
        )
        .order_by(Booking.desired_date, Booking.desired_time)
        .limit(12)
        .all()
    )
    recently_completed = (
        db.query(Booking)
        .filter(Booking.status == BookingStatus.completed)
        .order_by(Booking.desired_date.desc(), Booking.desired_time.desc())
        .limit(8)
        .all()
    )

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "salon_name": settings.salon_name,
        "count_new": count_new,
        "count_today": count_today,
        "count_tomorrow": count_tomorrow,
        "count_active": count_active,
        "new_bookings": new_bookings,
        "upcoming": upcoming,
        "recently_completed": recently_completed,
        "today": today,
    })


# ─── Bookings ─────────────────────────────────────────────────────────────────

@router.get("/bookings", response_class=HTMLResponse)
def admin_bookings(
    request: Request,
    filter_date: Optional[str] = None,
    filter_status: Optional[str] = None,
    filter_master: Optional[int] = None,
    filter_service: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir

    q = db.query(Booking)
    selected_date = None

    if filter_date:
        try:
            selected_date = date.fromisoformat(filter_date)
            q = q.filter(Booking.desired_date == selected_date)
        except ValueError:
            pass
    if filter_status:
        q = q.filter(Booking.status == filter_status)
    if filter_master:
        q = q.filter(Booking.master_id == filter_master)
    if filter_service:
        q = q.filter(Booking.service_id == filter_service)
    if search:
        q = q.join(Client).filter(
            Client.name.ilike(f"%{search}%") | Client.phone.ilike(f"%{search}%")
        )

    bookings = q.order_by(Booking.desired_date.desc(), Booking.desired_time.desc()).limit(200).all()

    # Day schedule grouped by master
    day_schedule = {}
    if selected_date:
        day_bookings = (
            db.query(Booking)
            .filter(
                Booking.desired_date == selected_date,
                Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
            )
            .order_by(Booking.desired_time)
            .all()
        )
        for b in day_bookings:
            mname = b.master.name if b.master else "Без преподавателя"
            day_schedule.setdefault(mname, []).append(b)

    masters = db.query(Master).filter(Master.is_active == True).all()
    services = db.query(Service).filter(Service.is_active == True).all()

    return templates.TemplateResponse(request, "admin/bookings.html", {
        "salon_name": settings.salon_name,
        "bookings": bookings,
        "masters": masters,
        "services": services,
        "filter_date": filter_date or "",
        "filter_status": filter_status or "",
        "filter_master": filter_master,
        "filter_service": filter_service,
        "search": search or "",
        "statuses": list(BookingStatus),
        "day_schedule": day_schedule,
        "selected_date": selected_date,
    })


@router.get("/bookings/{booking_id}", response_class=HTMLResponse)
def admin_booking_detail(booking_id: int, request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return RedirectResponse("/admin/bookings", status_code=302)
    masters = db.query(Master).filter(Master.is_active == True).all()
    return templates.TemplateResponse(request, "admin/booking_detail.html", {
        "salon_name": settings.salon_name,
        "booking": booking,
        "masters": masters,
        "statuses": list(BookingStatus),
    })


@router.post("/bookings/{booking_id}/status")
async def admin_change_status(
    booking_id: int,
    request: Request,
    new_status: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return RedirectResponse("/admin/bookings", status_code=302)

    old_status = booking.status
    try:
        booking.status = BookingStatus(new_status)
    except ValueError:
        return RedirectResponse(f"/admin/bookings/{booking_id}", status_code=302)

    db.commit()
    db.refresh(booking)

    try:
        update_event(booking)
    except Exception:
        pass

    if booking.status == BookingStatus.confirmed and old_status != BookingStatus.confirmed:
        asyncio.create_task(notify_booking_confirmed(booking))
    elif booking.status == BookingStatus.completed and old_status != BookingStatus.completed:
        asyncio.create_task(notify_booking_completed(booking))

    return RedirectResponse(f"/admin/bookings/{booking_id}", status_code=302)


# ─── Clients ──────────────────────────────────────────────────────────────────

@router.get("/clients", response_class=HTMLResponse)
def admin_clients(request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    clients = db.query(Client).order_by(Client.last_seen_at.desc()).all()
    return templates.TemplateResponse(request, "admin/clients.html", {
        "salon_name": settings.salon_name,
        "clients": clients,
    })


@router.get("/clients/{client_id}", response_class=HTMLResponse)
def admin_client_detail(client_id: int, request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return RedirectResponse("/admin/clients", status_code=302)
    bookings = (
        db.query(Booking)
        .filter(Booking.client_id == client_id)
        .order_by(Booking.desired_date.desc(), Booking.desired_time.desc())
        .all()
    )
    total_spent = sum(
        b.service.price for b in bookings if b.status == BookingStatus.completed
    )
    return templates.TemplateResponse(request, "admin/client_detail.html", {
        "salon_name": settings.salon_name,
        "client": client,
        "bookings": bookings,
        "total_spent": total_spent,
    })


# ─── Services ─────────────────────────────────────────────────────────────────

@router.get("/services", response_class=HTMLResponse)
def admin_services(request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    services = db.query(Service).order_by(Service.category, Service.id).all()
    return templates.TemplateResponse(request, "admin/services.html", {
        "salon_name": settings.salon_name,
        "services": services,
    })


@router.post("/services/create")
def admin_service_create(
    request: Request,
    category: str = Form(...),
    name: str = Form(...),
    price: int = Form(...),
    duration: int = Form(...),
    description: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    svc = Service(
        category=category, name=name, price=price, duration=duration,
        description=description or None, is_active=is_active,
    )
    db.add(svc)
    db.commit()
    return RedirectResponse("/admin/services", status_code=302)


@router.post("/services/{service_id}/edit")
def admin_service_edit(
    service_id: int,
    request: Request,
    category: str = Form(...),
    name: str = Form(...),
    price: int = Form(...),
    duration: int = Form(...),
    description: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    svc = db.query(Service).filter(Service.id == service_id).first()
    if svc:
        svc.category = category
        svc.name = name
        svc.price = price
        svc.duration = duration
        svc.description = description or None
        svc.is_active = is_active
        db.commit()
    return RedirectResponse("/admin/services", status_code=302)


@router.post("/services/{service_id}/toggle")
def admin_service_toggle(service_id: int, request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    svc = db.query(Service).filter(Service.id == service_id).first()
    if svc:
        svc.is_active = not svc.is_active
        db.commit()
    return RedirectResponse("/admin/services", status_code=302)


# ─── Masters ──────────────────────────────────────────────────────────────────

@router.get("/masters", response_class=HTMLResponse)
def admin_masters(request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    masters = db.query(Master).order_by(Master.id).all()
    services = db.query(Service).filter(Service.is_active == True).order_by(Service.category, Service.id).all()

    month_start = today_local().replace(day=1)
    master_stats = {}
    for m in masters:
        count = db.query(Booking).filter(
            Booking.master_id == m.id,
            Booking.desired_date >= month_start,
            Booking.status.in_([BookingStatus.confirmed, BookingStatus.completed]),
        ).count()
        master_stats[m.id] = count

    master_service_ids = {}
    for m in masters:
        master_service_ids[m.id] = [ms.service_id for ms in m.master_services]

    return templates.TemplateResponse(request, "admin/masters.html", {
        "salon_name": settings.salon_name,
        "masters": masters,
        "services": services,
        "master_stats": master_stats,
        "master_service_ids": master_service_ids,
    })


@router.post("/masters/create")
def admin_master_create(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    master = Master(name=name, is_active=True)
    db.add(master)
    db.commit()
    return RedirectResponse("/admin/masters", status_code=302)


@router.post("/masters/{master_id}/edit")
def admin_master_edit(
    master_id: int,
    request: Request,
    name: str = Form(...),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    master = db.query(Master).filter(Master.id == master_id).first()
    if master:
        master.name = name
        master.is_active = is_active
        db.commit()
    return RedirectResponse("/admin/masters", status_code=302)


@router.post("/masters/{master_id}/services")
async def admin_master_services(
    master_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    form = await request.form()
    service_ids = [int(v) for k, v in form.multi_items() if k == "service_ids"]

    db.query(MasterService).filter(MasterService.master_id == master_id).delete()
    for sid in service_ids:
        db.add(MasterService(master_id=master_id, service_id=sid))
    db.commit()
    return RedirectResponse("/admin/masters", status_code=302)


# ─── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics", response_class=HTMLResponse)
def admin_analytics(request: Request, period: str = "week", db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir

    today = today_local()
    if period == "today":
        start = today
        end = today
    elif period == "yesterday":
        start = today - timedelta(days=1)
        end = today - timedelta(days=1)
    elif period == "month":
        start = today.replace(day=1)
        end = today
    else:  # week
        start = today - timedelta(days=6)
        end = today

    def bq():
        return db.query(Booking).filter(
            Booking.desired_date >= start, Booking.desired_date <= end
        )

    total = bq().count()
    by_status = {s.value: bq().filter(Booking.status == s).count() for s in BookingStatus}
    revenue_fact = sum(
        b.service.price for b in bq().filter(Booking.status == BookingStatus.completed).all()
    )
    revenue_plan = sum(
        b.service.price for b in bq().filter(
            Booking.status.in_([BookingStatus.confirmed, BookingStatus.completed])
        ).all()
    )
    avg_check = round(revenue_fact / by_status["completed"], 0) if by_status["completed"] else 0
    cancel_pct = round(by_status["cancelled"] / total * 100, 1) if total else 0

    new_clients = db.query(Client).filter(
        Client.first_seen_at >= datetime.combine(start, datetime.min.time()),
        Client.first_seen_at <= datetime.combine(end, datetime.max.time()),
    ).count()
    repeat_clients = db.query(Client).filter(Client.visits_count > 1).count()

    # load by weekday
    weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    weekday_counts = [0] * 7
    all_period = bq().filter(Booking.status != BookingStatus.cancelled).all()
    for b in all_period:
        weekday_counts[b.desired_date.weekday()] += 1
    load_by_day = list(zip(weekday_names, weekday_counts))

    # top masters
    top_masters_raw = (
        db.query(Master.name, func.count(Booking.id).label("cnt"))
        .join(Booking, Booking.master_id == Master.id)
        .filter(Booking.desired_date >= start, Booking.desired_date <= end)
        .filter(Booking.status != BookingStatus.cancelled)
        .group_by(Master.id)
        .order_by(func.count(Booking.id).desc())
        .limit(5)
        .all()
    )
    top_masters = [(r.name, r.cnt) for r in top_masters_raw]

    # top services
    top_services_raw = (
        db.query(Service.name, func.count(Booking.id).label("cnt"))
        .join(Booking, Booking.service_id == Service.id)
        .filter(Booking.desired_date >= start, Booking.desired_date <= end)
        .filter(Booking.status != BookingStatus.cancelled)
        .group_by(Service.id)
        .order_by(func.count(Booking.id).desc())
        .limit(5)
        .all()
    )
    top_services = [(r.name, r.cnt) for r in top_services_raw]

    reviews = (
        db.query(BookingReview)
        .order_by(BookingReview.created_at.desc())
        .limit(10)
        .all()
    )
    avg_rating_row = db.query(func.avg(BookingReview.rating)).scalar()
    avg_rating = round(avg_rating_row, 2) if avg_rating_row else None

    return templates.TemplateResponse(request, "admin/analytics.html", {
        "salon_name": settings.salon_name,
        "period": period,
        "start": start,
        "end": end,
        "total": total,
        "by_status": by_status,
        "revenue_fact": revenue_fact,
        "revenue_plan": revenue_plan,
        "avg_check": avg_check,
        "cancel_pct": cancel_pct,
        "new_clients": new_clients,
        "repeat_clients": repeat_clients,
        "load_by_day": load_by_day,
        "top_masters": top_masters,
        "top_services": top_services,
        "reviews": reviews,
        "avg_rating": avg_rating,
    })


# ─── Broadcasts ───────────────────────────────────────────────────────────────

@router.get("/broadcasts", response_class=HTMLResponse)
def admin_broadcasts(request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    broadcasts = db.query(Broadcast).order_by(Broadcast.created_at.desc()).all()
    services = db.query(Service).filter(Service.is_active == True).all()
    masters = db.query(Master).filter(Master.is_active == True).all()
    return templates.TemplateResponse(request, "admin/broadcasts.html", {
        "salon_name": settings.salon_name,
        "broadcasts": broadcasts,
        "services": services,
        "masters": masters,
    })


@router.post("/broadcasts/create")
def admin_broadcast_create(
    request: Request,
    title: str = Form(...),
    message_text: str = Form(...),
    image_url: str = Form(""),
    audience_type: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    bc = Broadcast(
        title=title,
        message_text=message_text,
        image_url=image_url or None,
        audience_type=audience_type,
        status=BroadcastStatus.draft,
    )
    db.add(bc)
    db.commit()
    return RedirectResponse("/admin/broadcasts", status_code=302)


@router.post("/broadcasts/{broadcast_id}/send")
async def admin_broadcast_send(
    broadcast_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = check_auth(request)
    if redir:
        return redir
    from app.services.broadcasts import send_broadcast
    asyncio.create_task(send_broadcast(db, broadcast_id))
    return RedirectResponse("/admin/broadcasts", status_code=302)


# ─── Reviews ──────────────────────────────────────────────────────────────────

@router.get("/reviews", response_class=HTMLResponse)
def admin_reviews(request: Request, db: Session = Depends(get_db)):
    redir = check_auth(request)
    if redir:
        return redir
    reviews = (
        db.query(BookingReview)
        .order_by(BookingReview.created_at.desc())
        .all()
    )
    avg = db.query(func.avg(BookingReview.rating)).scalar()
    avg_rating = round(avg, 2) if avg else None
    return templates.TemplateResponse(request, "admin/reviews.html", {
        "salon_name": settings.salon_name,
        "reviews": reviews,
        "avg_rating": avg_rating,
    })


# ─── Settings ─────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
def admin_settings(request: Request):
    redir = check_auth(request)
    if redir:
        return redir
    return templates.TemplateResponse(request, "admin/settings.html", {
        "salon_name": settings.salon_name,
        "settings": settings,
    })


@router.get("/calendar-test")
def admin_calendar_test(request: Request):
    redir = check_auth(request)
    if redir:
        return redir
    from app.services.calendar import _get_service
    svc = _get_service()
    if not svc:
        if not settings.google_service_account_file and not settings.google_service_account_json:
            return {"ok": False, "error": "Не настроен GOOGLE_SERVICE_ACCOUNT_FILE или GOOGLE_SERVICE_ACCOUNT_JSON в .env"}
        return {"ok": False, "error": "Не удалось создать Google Calendar клиент. Проверь логи сервера."}
    try:
        result = svc.calendarList().list().execute()
        calendars = [{"id": c["id"], "summary": c.get("summary", "")} for c in result.get("items", [])]
        try:
            svc.calendars().get(calendarId=settings.calendar_id).execute()
            calendar_ok = True
            calendar_error = None
        except Exception as e:
            calendar_ok = False
            calendar_error = str(e)
        return {
            "ok": calendar_ok,
            "calendar_id": settings.calendar_id,
            "calendar_error": calendar_error,
            "available_calendars": calendars,
            "hint": "Если calendar_ok=False — поделись нужным календарём с сервисным аккаунтом и укажи его ID в .env",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
