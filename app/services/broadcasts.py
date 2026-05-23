import logging
from datetime import datetime, timedelta

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


async def _send_message(chat_id: int, text: str, image_url: str | None = None) -> bool:
    token = settings.telegram_bot_token
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if image_url:
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{token}/sendPhoto",
                    json={"chat_id": chat_id, "photo": image_url, "caption": text, "parse_mode": "HTML"},
                )
            else:
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                )
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Broadcast send failed for {chat_id}: {e}")
        return False


def _get_audience(db, audience_type: str):
    from app.models import Client, Booking, BookingStatus
    now = datetime.utcnow()
    q = db.query(Client).filter(Client.telegram_user_id != None)

    if audience_type == "all":
        return q.all()
    elif audience_type == "new_clients":
        return q.filter(Client.visits_count == 1).all()
    elif audience_type == "regular_clients":
        return q.filter(Client.visits_count >= 3).all()
    elif audience_type == "inactive_clients":
        cutoff = now - timedelta(days=60)
        return q.filter(Client.last_seen_at < cutoff).all()
    elif audience_type.startswith("service:"):
        service_id = int(audience_type.split(":")[1])
        client_ids = (
            db.query(Booking.client_id)
            .filter(Booking.service_id == service_id)
            .distinct()
            .all()
        )
        ids = [r[0] for r in client_ids]
        return q.filter(Client.id.in_(ids)).all()
    elif audience_type.startswith("master:"):
        master_id = int(audience_type.split(":")[1])
        client_ids = (
            db.query(Booking.client_id)
            .filter(Booking.master_id == master_id)
            .distinct()
            .all()
        )
        ids = [r[0] for r in client_ids]
        return q.filter(Client.id.in_(ids)).all()
    return []


async def send_broadcast(db, broadcast_id: int) -> int:
    from app.models import Broadcast, BroadcastStatus
    broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
    if not broadcast:
        return 0

    broadcast.status = BroadcastStatus.sending
    db.commit()

    clients = _get_audience(db, broadcast.audience_type)
    sent = 0
    for client in clients:
        ok = await _send_message(client.telegram_user_id, broadcast.message_text, broadcast.image_url)
        if ok:
            sent += 1

    broadcast.status = BroadcastStatus.sent
    broadcast.sent_at = datetime.utcnow()
    db.commit()
    return sent
