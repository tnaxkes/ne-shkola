import logging
from datetime import datetime, timedelta
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

COLOR_MAP = {
    "new": "5",
    "confirmed": "10",
    "cancelled": "11",
    "completed": "9",
}


def _get_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        import json

        creds = None
        if settings.google_service_account_json:
            info = json.loads(settings.google_service_account_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/calendar"],
            )
        elif settings.google_service_account_file:
            creds = service_account.Credentials.from_service_account_file(
                settings.google_service_account_file,
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
        else:
            return None
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.warning(f"Google Calendar init failed: {e}")
        return None


def create_event(booking) -> Optional[str]:
    svc = _get_service()
    if not svc:
        return None
    try:
        start_dt = datetime.combine(booking.desired_date, booking.desired_time)
        end_dt = start_dt + timedelta(minutes=booking.service.duration)
        master_name = booking.master.name if booking.master else "не назначен"
        event = {
            "summary": f"[NEW] {booking.service.name} — {booking.client.name}",
            "description": (
                f"Клиент: {booking.client.name}\n"
                f"Телефон: {booking.client.phone}\n"
                f"Программа: {booking.service.name}\n"
                f"Преподаватель: {master_name}\n"
                f"Дата: {booking.desired_date}\n"
                f"Время: {booking.desired_time}\n"
                f"Комментарий: {booking.comment or '-'}\n"
                f"Источник: Telegram"
            ),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": settings.app_timezone},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": settings.app_timezone},
            "colorId": COLOR_MAP["new"],
        }
        result = svc.events().insert(calendarId=settings.calendar_id, body=event).execute()
        return result.get("id")
    except Exception as e:
        logger.warning(f"Calendar create_event failed: {e}")
        return None


def update_event(booking) -> None:
    svc = _get_service()
    if not svc or not booking.calendar_event_id:
        return
    try:
        status_label = booking.status.value.upper()
        master_name = booking.master.name if booking.master else "не назначен"
        start_dt = datetime.combine(booking.desired_date, booking.desired_time)
        end_dt = start_dt + timedelta(minutes=booking.service.duration)
        event = {
            "summary": f"[{status_label}] {booking.service.name} — {booking.client.name}",
            "description": (
                f"Клиент: {booking.client.name}\n"
                f"Телефон: {booking.client.phone}\n"
                f"Программа: {booking.service.name}\n"
                f"Преподаватель: {master_name}\n"
                f"Дата: {booking.desired_date}\n"
                f"Время: {booking.desired_time}\n"
                f"Комментарий: {booking.comment or '-'}\n"
                f"Источник: Telegram"
            ),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": settings.app_timezone},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": settings.app_timezone},
            "colorId": COLOR_MAP.get(booking.status.value, "5"),
        }
        svc.events().update(
            calendarId=settings.calendar_id,
            eventId=booking.calendar_event_id,
            body=event,
        ).execute()
    except Exception as e:
        logger.warning(f"Calendar update_event failed: {e}")
