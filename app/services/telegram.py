import logging

import httpx

from app.config import settings
from app.timeutils import format_date_ru, format_time

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


async def _send(chat_id: str | int, text: str, reply_markup: dict | None = None) -> None:
    if not settings.telegram_bot_token or not chat_id:
        return
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)
    url = f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


async def notify_booking_confirmed(booking) -> None:
    if not booking.client.telegram_user_id:
        return
    master_name = booking.master.name if booking.master else "уточним в школе"
    text = (
        f"<b>Ваша запись подтверждена</b>\n"
        f"{booking.service.name}\n"
        f"Дата: {format_date_ru(booking.desired_date)}\n"
        f"Время: {format_time(booking.desired_time)}\n"
        f"Преподаватель: {master_name}\n\n"
        f"Будем рады видеть вас в школе."
    )
    await _send(booking.client.telegram_user_id, text)


async def notify_booking_completed(booking) -> None:
    if not booking.client.telegram_user_id:
        return
    text = (
        f"<b>Спасибо за занятие</b>\n"
        f"Благодарим, что выбрали Не Школу Барабанов.\n"
        f"Будем рады видеть вас снова. Оцените, пожалуйста, занятие и оставьте короткий отзыв."
    )
    markup = {
        "inline_keyboard": [[
            {"text": "Оставить отзыв", "callback_data": f"review:{booking.id}"}
        ]]
    }
    await _send(booking.client.telegram_user_id, text, markup)


async def notify_admin_new_booking(booking) -> None:
    if not settings.admin_telegram_chat_id:
        return
    master_name = booking.master.name if booking.master else "Не важно"
    text = (
        f"<b>Новая заявка</b>\n"
        f"Клиент: {booking.client.name}\n"
        f"Телефон: {booking.client.phone}\n"
        f"Программа: {booking.service.name}\n"
        f"Преподаватель: {master_name}\n"
        f"Дата: {format_date_ru(booking.desired_date)}\n"
        f"Время: {format_time(booking.desired_time)}\n"
        f"Комментарий: {booking.comment or '-'}"
    )
    await _send(settings.admin_telegram_chat_id, text)


async def send_reminder_24h(booking) -> None:
    if not booking.client.telegram_user_id:
        return
    master_name = booking.master.name if booking.master else "уточним в школе"
    text = (
        f"<b>Напоминание о занятии</b>\n"
        f"Завтра в {format_time(booking.desired_time)} вас ожидает {booking.service.name}.\n"
        f"Преподаватель: {master_name}."
    )
    await _send(booking.client.telegram_user_id, text)


async def send_reminder_2h(booking) -> None:
    if not booking.client.telegram_user_id:
        return
    text = (
        f"<b>Скоро ваше занятие</b>\n"
        f"Через пару часов вас ожидает {booking.service.name} в {format_time(booking.desired_time)}.\n"
        f"Адрес: {settings.salon_address}"
    )
    await _send(booking.client.telegram_user_id, text)


async def send_campaign_21d(client) -> None:
    text = (
        f"<b>Для вас персональное предложение</b>\n"
        f"Если захотите продолжить занятия или записаться повторно, администратор поможет подобрать удобное время и программу."
    )
    await _send(client.telegram_user_id, text)


async def send_campaign_30d(client) -> None:
    text = (
        f"<b>Пора вернуться к занятиям</b>\n"
        f"Если хотите продолжить — подберём удобное время и подходящую программу."
    )
    await _send(client.telegram_user_id, text)


async def send_campaign_60d(client) -> None:
    text = (
        f"<b>Давно не виделись</b>\n"
        f"Будем рады снова видеть вас в школе. Уже подготовили удобные окна для записи."
    )
    await _send(client.telegram_user_id, text)
