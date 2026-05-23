"""
Telegram-бот «Не Школа Барабанов».
Запуск: python -m app.bot.telegram_bot
Бот общается с API через HTTP (httpx), не через прямой импорт моделей.
"""
import asyncio
import logging
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# When embedded in uvicorn (Railway), call localhost directly to avoid external roundtrip.
# When running standalone locally, fall back to app_url.
import os as _os
_port = _os.environ.get("PORT", str(settings.run_port))
API_BASE = f"http://localhost:{_port}" if "localhost" in settings.app_url else settings.app_url

# ─── Тексты ──────────────────────────────────────────────────────────────────

MASTER_INTROS = {
    "Алексей Громов": "Джаз, рок, блюз и всё между ними. 15 лет выступлений и преподавания — учит слышать музыку, а не просто считать доли.",
    "Мария Темникова": "Современные стили, электронные пэды и программирование ритмов. Помогает найти свой звук в сегодняшней музыке.",
    "Дмитрий Кравцов": "Академическая база плюс латина и фанк. Фокус на технику постановки рук и независимость педали — с нуля до уверенного грува.",
}

MASTER_PHOTOS: dict = {}  # {"Алексей Громов": Path("photos/alexey.jpg")}

SERVICE_DESCRIPTIONS = {
    "Индивидуальные занятия": "Персональная программа под твой уровень, темп и цели. Без лишней теории — максимум живой практики за инструментом.",
    "Групповые занятия": "Играем вместе с другими музыкантами. Учимся держать ритм в ансамбле и слышать общую картину.",
    "Мастер-классы": "Конкретная тема, конкретный результат. Берём один навык и разбираем его детально за одну сессию.",
    "Интенсивы": "Максимальная концентрация работы за ограниченное время. Подходит для быстрого скачка в технике или подготовки к выступлению.",
}

STATUS_LABELS = {
    "new": "Ожидает подтверждения",
    "confirmed": "Подтверждена",
    "cancelled": "Отменена",
    "completed": "Завершена",
}

# ─── FSM States ───────────────────────────────────────────────────────────────

class BookingStates(StatesGroup):
    selecting_category = State()
    selecting_service = State()
    selecting_master = State()
    selecting_date = State()
    selecting_time = State()
    entering_name = State()
    entering_phone = State()
    entering_comment = State()
    confirming = State()


class RescheduleStates(StatesGroup):
    selecting_date = State()
    selecting_time = State()


class ReviewStates(StatesGroup):
    entering_rating = State()
    entering_comment = State()


# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Программы"), KeyboardButton(text="Записаться")],
            [KeyboardButton(text="О школе"), KeyboardButton(text="Моя запись")],
        ],
        resize_keyboard=True,
    )


def inline_kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """rows — list of rows, each row is list of (text, callback_data)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=cd) for t, cd in row]
            for row in rows
        ]
    )


def date_keyboard(prefix: str = "date", days: int = 14) -> InlineKeyboardMarkup:
    today = date.today()
    buttons = []
    row = []
    for i in range(days):
        d = today + timedelta(days=i)
        label = d.strftime("%d.%m") + (" (сег)" if i == 0 else "")
        row.append(InlineKeyboardButton(text=label, callback_data=f"{prefix}:{d.isoformat()}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def api_get(path: str, params: dict = None) -> dict | list | None:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                r = await client.get(f"{API_BASE}{path}", params=params or {})
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.warning(f"API GET {path} attempt {attempt+1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(1)
    return None


async def api_post(path: str, json: dict = None, params: dict = None) -> dict | None:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                r = await client.post(f"{API_BASE}{path}", json=json or {}, params=params or {})
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                raw = e.response.json().get("detail", "")
                detail = raw if isinstance(raw, str) else str(raw)
            except Exception:
                pass
            logger.warning(f"API POST {path} error: {e} — {detail}")
            return {"error": detail or str(e)}
        except Exception as e:
            logger.warning(f"API POST {path} attempt {attempt+1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(1)
    return {"error": "Сервер временно недоступен. Попробуйте ещё раз."}


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def booking_card(b: dict) -> str:
    status = STATUS_LABELS.get(b.get("status", ""), b.get("status", ""))
    master = b.get("master_name") or "уточним в школе"
    d = b.get("desired_date", "")
    t = b.get("desired_time", "")
    try:
        d_fmt = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        d_fmt = d
    return (
        f"📅 {d_fmt} в {t}\n"
        f"🥁 {b.get('service_name', '')}\n"
        f"👤 Преподаватель: {master}\n"
        f"📌 Статус: {status}"
    )


# ─── Router ───────────────────────────────────────────────────────────────────

router = Router()


# ─── /cancel — сброс состояния ────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


# ─── Глобальный обработчик зависших callback-ов ───────────────────────────────

@router.errors()
async def global_error_handler(event, exception):
    logger.error(f"Unhandled error: {exception}")
    return True


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    cfg = await api_get("/api/settings") or {}
    text = (
        f"<b>Добро пожаловать в Не Школу Барабанов</b>\n\n"
        f"Здесь можно посмотреть программы занятий, выбрать преподавателя и записаться на удобное время — без звонков и лишних переписок.\n\n"
        f"Не Школа Барабанов — это живая практика, внимание к технике и занятия в своём темпе.\n\n"
        f"Основные команды:\n\n"
        f"/services — программы занятий, стоимость и длительность.\n"
        f"/book — запись на занятие с выбором преподавателя, даты и времени.\n"
        f"/masters — информация о преподавателях.\n"
        f"/visit — ваша запись: дата, время и статус.\n"
        f"/about — о школе и контакты.\n\n"
        f"Наши контакты:\n"
        f"Адрес: {cfg.get('salon_address', settings.salon_address)}\n"
        f"Телефон: {cfg.get('salon_phone', settings.salon_phone)}\n"
        f"Связь: {cfg.get('salon_contacts', settings.salon_contacts)}\n\n"
        f"Для просмотра программ используйте /services.\n"
        f"Для быстрой записи нажмите /book.\n\n"
        f"Будем рады видеть вас в Не Школе Барабанов."
    )
    await message.answer(text, reply_markup=main_menu(), parse_mode=ParseMode.HTML)


# ─── /about ───────────────────────────────────────────────────────────────────

@router.message(Command("about"))
@router.message(F.text == "О школе")
async def cmd_about(message: Message):
    cfg = await api_get("/api/settings") or {}
    text = (
        f"<b>Не Школа Барабанов</b>\n\n"
        f"Пространство, где учатся играть — а не просто считают доли. Живая практика, внимание к технике и индивидуальный подход независимо от уровня.\n\n"
        f"Адрес: {cfg.get('salon_address', settings.salon_address)}\n"
        f"Телефон: {cfg.get('salon_phone', settings.salon_phone)}\n"
        f"Связь с администратором: {cfg.get('salon_contacts', settings.salon_contacts)}\n\n"
        f"Если нужен персональный подбор программы или преподавателя, администратор поможет в переписке."
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


# ─── /services ────────────────────────────────────────────────────────────────

@router.message(Command("services"))
@router.message(F.text == "Программы")
async def cmd_services(message: Message):
    data = await api_get("/api/services")
    if not data:
        await message.answer("Не удалось загрузить программы. Попробуйте позже.")
        return

    categories: dict[str, list] = {}
    for svc in data:
        categories.setdefault(svc["category"], []).append(svc)

    text = "<b>Программы занятий</b>\n"
    for cat, items in categories.items():
        desc = SERVICE_DESCRIPTIONS.get(cat, "")
        text += f"\n<b>{cat}</b>\n"
        if desc:
            text += f"<i>{desc}</i>\n"
        for svc in items:
            dur = svc["duration"]
            dur_str = f"{dur} мин" if dur < 120 else f"{dur // 60} ч"
            text += f"• {svc['name']} — {svc['price']} ₽ · {dur_str}\n"
            if svc.get("description"):
                text += f"  <i>{svc['description']}</i>\n"

    text += "\nДля записи используйте /book"
    await message.answer(text, parse_mode=ParseMode.HTML)


# ─── /masters ─────────────────────────────────────────────────────────────────

@router.message(Command("masters"))
async def cmd_masters(message: Message):
    data = await api_get("/api/masters")
    if not data:
        await message.answer("Не удалось загрузить список преподавателей.")
        return
    text = "<b>Преподаватели школы</b>\nВыберите специалиста, чтобы открыть профиль."
    rows = [[( m["name"], f"master_info:{m['id']}:{m['name']}" )] for m in data]
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=inline_kb(rows))


@router.callback_query(F.data.startswith("master_info:"))
async def cb_master_info(callback: CallbackQuery):
    parts = callback.data.split(":", 2)
    name = parts[2] if len(parts) > 2 else ""
    intro = MASTER_INTROS.get(name, "Информация о преподавателе недоступна.")
    text = f"<b>{name}</b>\n\n{intro}"

    photo_path = MASTER_PHOTOS.get(name)
    if photo_path and Path(photo_path).exists():
        from aiogram.types import FSInputFile
        await callback.message.answer_photo(FSInputFile(photo_path), caption=text, parse_mode=ParseMode.HTML)
    else:
        await callback.message.answer(text, parse_mode=ParseMode.HTML)
    await callback.answer()


# ─── /book — начало записи ────────────────────────────────────────────────────

@router.message(Command("book"))
@router.message(F.text == "Записаться")
async def cmd_book(message: Message, state: FSMContext):
    await state.clear()
    data = await api_get("/api/services")
    if not data:
        await message.answer("Не удалось загрузить программы. Попробуйте позже.")
        return

    categories = sorted(set(s["category"] for s in data))
    rows = [[(cat, f"category:{cat}")] for cat in categories]
    rows.append([("❌ Отменить", "cancel_booking")])

    await message.answer(
        "Запись на занятие.\nСначала выберите направление, затем программу, преподавателя и удобное время.",
        reply_markup=inline_kb(rows),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(BookingStates.selecting_category)
    await state.update_data(all_services=data)


@router.callback_query(BookingStates.selecting_category, F.data.startswith("category:"))
async def cb_select_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    data = await state.get_data()
    services = [s for s in data.get("all_services", []) if s["category"] == category]

    rows = []
    for svc in services:
        dur = svc["duration"]
        dur_str = f"{dur} мин" if dur < 120 else f"{dur // 60} ч"
        label = f"{svc['name']} — {svc['price']} ₽ · {dur_str}"
        rows.append([(label, f"service:{svc['id']}")])
    rows.append([("« Назад", "back_to_categories"), ("❌ Отменить", "cancel_booking")])

    await callback.message.edit_text(
        f"<b>{category}</b>\nВыберите программу:",
        reply_markup=inline_kb(rows),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(BookingStates.selecting_service)
    await state.update_data(selected_category=category)
    await callback.answer()


@router.callback_query(BookingStates.selecting_service, F.data == "back_to_categories")
async def cb_back_to_categories(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    all_services = data.get("all_services", [])
    categories = sorted(set(s["category"] for s in all_services))
    rows = [[(cat, f"category:{cat}")] for cat in categories]
    rows.append([("❌ Отменить", "cancel_booking")])
    await callback.message.edit_text(
        "Запись на занятие.\nСначала выберите направление, затем программу, преподавателя и удобное время.",
        reply_markup=inline_kb(rows),
    )
    await state.set_state(BookingStates.selecting_category)
    await callback.answer()


@router.callback_query(BookingStates.selecting_service, F.data.startswith("service:"))
async def cb_select_service(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    all_services = data.get("all_services", [])
    service = next((s for s in all_services if s["id"] == service_id), None)
    if not service:
        await callback.answer("Программа не найдена")
        return

    masters_data = await api_get("/api/masters", {"service_id": service_id})
    rows = []
    if masters_data:
        for m in masters_data:
            rows.append([(m["name"], f"master:{m['id']}:{m['name']}")])
    rows.append([("🎯 Подобрать преподавателя", "master:0:auto")])
    rows.append([("❌ Отменить", "cancel_booking")])

    await callback.message.edit_text(
        f"<b>{service['name']}</b>\nВыберите преподавателя или доверьте подбор школе.",
        reply_markup=inline_kb(rows),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(BookingStates.selecting_master)
    await state.update_data(service_id=service_id, service_name=service["name"])
    await callback.answer()


@router.callback_query(BookingStates.selecting_master, F.data.startswith("master:"))
async def cb_select_master(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    master_id = int(parts[1])
    master_name = parts[2] if len(parts) > 2 else "auto"

    await state.update_data(
        master_id=master_id if master_id != 0 else None,
        master_name=master_name if master_id != 0 else "Подберём для вас",
    )
    await callback.message.edit_text(
        "Выберите дату занятия",
        reply_markup=date_keyboard("date"),
    )
    await state.set_state(BookingStates.selecting_date)
    await callback.answer()


@router.callback_query(BookingStates.selecting_date, F.data.startswith("date:"))
async def cb_select_date(callback: CallbackQuery, state: FSMContext):
    d_str = callback.data.split(":", 1)[1]
    fsm_data = await state.get_data()
    service_id = fsm_data.get("service_id")
    master_id = fsm_data.get("master_id")

    params = {"service_id": service_id, "desired_date": d_str}
    if master_id:
        params["master_id"] = master_id

    slots_data = await api_get("/api/slots", params)
    slots = slots_data.get("slots", []) if slots_data else []

    if not slots:
        await callback.answer("На эту дату свободных слотов нет.", show_alert=True)
        return

    rows = []
    row = []
    for slot in slots:
        row.append(InlineKeyboardButton(text=slot, callback_data=f"time:{slot}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking")])

    await callback.message.edit_text(
        f"📅 {datetime.strptime(d_str, '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.update_data(desired_date=d_str)
    await state.set_state(BookingStates.selecting_time)
    await callback.answer()


@router.callback_query(BookingStates.selecting_time, F.data.startswith("time:"))
async def cb_select_time(callback: CallbackQuery, state: FSMContext):
    t_str = callback.data.split(":", 1)[1]
    await state.update_data(desired_time=t_str)
    await callback.message.edit_text("Как к вам обращаться?", reply_markup=None)
    await state.set_state(BookingStates.entering_name)
    await callback.answer()


@router.message(BookingStates.entering_name)
async def booking_enter_name(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) < 2:
        await message.answer("Пожалуйста, введите имя (минимум 2 символа).")
        return
    await state.update_data(name=message.text.strip())
    await message.answer(
        "Оставьте номер телефона\nОн нужен для подтверждения записи.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(BookingStates.entering_phone)


@router.message(BookingStates.entering_phone)
async def booking_enter_phone(message: Message, state: FSMContext):
    phone = message.text.strip() if message.text else ""
    if len(phone) < 7:
        await message.answer("Введите корректный номер телефона.")
        return
    await state.update_data(phone=phone)
    await message.answer(
        "Если хотите, добавьте комментарий к записи одним сообщением.\nЕсли дополнений нет, просто отправьте: -"
    )
    await state.set_state(BookingStates.entering_comment)


@router.message(BookingStates.entering_comment)
async def booking_enter_comment(message: Message, state: FSMContext):
    comment = message.text.strip() if message.text else "-"
    await state.update_data(comment=comment)
    fsm_data = await state.get_data()

    d_str = fsm_data["desired_date"]
    t_str = fsm_data["desired_time"]
    try:
        d_fmt = datetime.strptime(d_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        d_fmt = d_str
    master_name = fsm_data.get("master_name", "Подберём для вас")

    summary = (
        f"<b>Подтверждение записи</b>\n"
        f"Программа: {fsm_data.get('service_name', '')}\n"
        f"Преподаватель: {master_name}\n"
        f"Дата: {d_fmt}\n"
        f"Время: {t_str}\n"
        f"Имя: {fsm_data.get('name', '')}\n"
        f"Телефон: {fsm_data.get('phone', '')}\n"
        f"Комментарий: {comment}"
    )
    kb = inline_kb([
        [("✅ Подтвердить запись", "confirm_booking"), ("❌ Отменить", "cancel_booking")]
    ])
    await message.answer(summary, reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(BookingStates.confirming)


@router.callback_query(BookingStates.confirming, F.data == "confirm_booking")
async def cb_confirm_booking(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    fsm_data = await state.get_data()

    # Проверяем что FSM-данные не устарели
    if not fsm_data.get("service_id") or not fsm_data.get("desired_date") or not fsm_data.get("desired_time"):
        await state.clear()
        await callback.message.answer("⚠️ Данные устарели. Начните заново — /book", reply_markup=main_menu())
        return

    try:
        await callback.message.edit_text("⏳ Оформляем запись...")
    except Exception:
        pass

    payload = {
        "telegram_user_id": callback.from_user.id,
        "service_id": fsm_data["service_id"],
        "desired_date": fsm_data["desired_date"],
        "desired_time": fsm_data["desired_time"] + ":00",
        "name": fsm_data["name"],
        "phone": fsm_data["phone"],
        "comment": fsm_data.get("comment"),
        "master_id": fsm_data.get("master_id"),
    }

    result = await api_post("/api/bookings", json=payload)
    await state.clear()

    if not result or "error" in result:
        raw_err = (result.get("error") if result else None) or ""
        err_text = str(raw_err).strip() if raw_err else "Не удалось создать запись. Попробуйте ещё раз."
        logger.warning("Booking failed: %s | payload: %s", raw_err, payload)
        await callback.message.answer(f"❌ {err_text}\n\n/book — начать заново", reply_markup=main_menu())
        return

    d_str = result.get("desired_date", "")
    try:
        d_fmt = datetime.strptime(d_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        d_fmt = d_str

    text = (
        f"✅ <b>Запись оформлена!</b>\n\n"
        f"📅 {d_fmt} в {result.get('desired_time', '')}\n"
        f"🥁 {result.get('service_name', '')}\n\n"
        f"Ждём вас! Посмотреть запись — /visit"
    )
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        await callback.message.answer(text, parse_mode=ParseMode.HTML)
    await callback.message.answer("Главное меню:", reply_markup=main_menu())


@router.callback_query(F.data == "cancel_booking")
async def cb_cancel_booking_form(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Запись отменена. Если захотите, можно начать заново в любое время."
    )
    await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()


# ─── /visit ───────────────────────────────────────────────────────────────────

@router.message(Command("visit"))
@router.message(F.text == "Моя запись")
async def cmd_visit(message: Message, state: FSMContext):
    await state.clear()
    data = await api_get("/api/bookings/me", {"telegram_user_id": message.from_user.id})
    if not data:
        await message.answer(
            "Записей пока нет.\nМожно сразу перейти к выбору программы и времени.",
            reply_markup=inline_kb([[("📅 Записаться", "start_booking")]]),
        )
        return

    active = data.get("booking")
    last = data.get("last_booking")

    if not active and not last:
        await message.answer(
            "Записей пока нет.\nМожно сразу перейти к выбору программы и времени.",
            reply_markup=inline_kb([[("📅 Записаться", "start_booking")]]),
        )
        return

    if not active:
        card = booking_card(last)
        await message.answer(
            f"Сейчас активной записи нет.\n\n<b>Последний визит</b>\n{card}",
            parse_mode=ParseMode.HTML,
            reply_markup=inline_kb([
                [
                    ("🔄 Записаться на эту программу", f"rebook:{last['service_id']}"),
                    ("📅 Записаться на другую программу", "start_booking"),
                ]
            ]),
        )
        return

    card = booking_card(active)
    await message.answer(
        card,
        parse_mode=ParseMode.HTML,
        reply_markup=inline_kb([
            [
                ("🔄 Перенести запись", f"reschedule:{active['id']}"),
                ("❌ Отменить запись", f"cancel_visit:{active['id']}"),
            ]
        ]),
    )


@router.callback_query(F.data == "start_booking")
async def cb_start_booking(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await cmd_book(callback.message, state)
    await callback.answer()


@router.callback_query(F.data.startswith("rebook:"))
async def cb_rebook(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split(":")[1])
    await state.clear()
    data = await api_get("/api/services")
    if not data:
        await callback.answer("Не удалось загрузить программы.", show_alert=True)
        return
    service = next((s for s in data if s["id"] == service_id), None)
    if not service:
        await callback.answer("Программа не найдена.", show_alert=True)
        return
    await state.update_data(all_services=data)

    masters_data = await api_get("/api/masters", {"service_id": service_id})
    rows = []
    if masters_data:
        for m in masters_data:
            rows.append([(m["name"], f"master:{m['id']}:{m['name']}")])
    rows.append([("🎯 Подобрать преподавателя", "master:0:auto")])
    rows.append([("❌ Отменить", "cancel_booking")])

    await callback.message.answer(
        f"<b>{service['name']}</b>\nВыберите преподавателя или доверьте подбор школе.",
        reply_markup=inline_kb(rows),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(BookingStates.selecting_master)
    await state.update_data(service_id=service_id, service_name=service["name"])
    await callback.answer()


@router.callback_query(F.data.startswith("reschedule:"))
async def cb_reschedule(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split(":")[1])
    await state.set_state(RescheduleStates.selecting_date)
    await state.update_data(reschedule_booking_id=booking_id)
    await callback.message.edit_text(
        "Выберите новую дату занятия",
        reply_markup=date_keyboard("reschedule_date"),
    )
    await callback.answer()


@router.callback_query(RescheduleStates.selecting_date, F.data.startswith("reschedule_date:"))
async def cb_reschedule_date(callback: CallbackQuery, state: FSMContext):
    d_str = callback.data.split(":", 1)[1]
    fsm_data = await state.get_data()
    booking_id = fsm_data.get("reschedule_booking_id")

    # get service from current booking
    history = await api_get("/api/bookings/me", {"telegram_user_id": callback.from_user.id})
    booking = history.get("booking") if history else None
    service_id = booking.get("service_id") if booking else None

    if not service_id:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    slots_data = await api_get("/api/slots", {"service_id": service_id, "desired_date": d_str})
    slots = slots_data.get("slots", []) if slots_data else []

    if not slots:
        await callback.answer("На эту дату свободных слотов нет.", show_alert=True)
        return

    rows = []
    row = []
    for slot in slots:
        row.append(InlineKeyboardButton(text=slot, callback_data=f"reschedule_time:{slot}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking")])

    await callback.message.edit_text(
        f"📅 {datetime.strptime(d_str, '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите новое время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.update_data(reschedule_date=d_str)
    await state.set_state(RescheduleStates.selecting_time)
    await callback.answer()


@router.callback_query(RescheduleStates.selecting_time, F.data.startswith("reschedule_time:"))
async def cb_reschedule_time(callback: CallbackQuery, state: FSMContext):
    t_str = callback.data.split(":", 1)[1]
    fsm_data = await state.get_data()
    booking_id = fsm_data.get("reschedule_booking_id")
    d_str = fsm_data.get("reschedule_date")

    result = await api_post(
        f"/api/bookings/{booking_id}/reschedule",
        json={"desired_date": d_str, "desired_time": t_str + ":00"},
        params={"telegram_user_id": callback.from_user.id},
    )
    await state.clear()

    if not result or "error" in result:
        err = result.get("error", "Ошибка при переносе.") if result else "Сервер недоступен."
        await callback.message.edit_text(f"❌ {err}")
        await callback.answer()
        return

    card = booking_card(result)
    await callback.message.edit_text(
        f"<b>Запись перенесена.</b>\n{card}",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_visit:"))
async def cb_cancel_visit(callback: CallbackQuery):
    booking_id = int(callback.data.split(":")[1])
    result = await api_post(
        f"/api/bookings/{booking_id}/cancel",
        params={"telegram_user_id": callback.from_user.id},
    )
    if result and "error" not in result:
        await callback.answer("Запись отменена.", show_alert=True)
        await callback.message.edit_text("❌ Запись отменена.")
    else:
        err = result.get("error", "Ошибка.") if result else "Сервер недоступен."
        await callback.answer(err, show_alert=True)


# ─── Отзыв ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("review:"))
async def cb_review_start(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split(":")[1])
    await state.set_state(ReviewStates.entering_rating)
    await state.update_data(review_booking_id=booking_id)
    kb = inline_kb([[
        ("1 ⭐", "rating:1"),
        ("2 ⭐", "rating:2"),
        ("3 ⭐", "rating:3"),
        ("4 ⭐", "rating:4"),
        ("5 ⭐", "rating:5"),
    ]])
    await callback.message.answer(
        "Оцените впечатление от занятия\nПо шкале от 1 до 5.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(ReviewStates.entering_rating, F.data.startswith("rating:"))
async def cb_review_rating(callback: CallbackQuery, state: FSMContext):
    rating = int(callback.data.split(":")[1])
    await state.update_data(review_rating=rating)
    await callback.message.edit_text(
        f"Оценка: {'⭐' * rating}\n\n"
        "Поделитесь коротким отзывом о занятии.\n"
        "Если не хотите добавлять текст, просто отправьте: -"
    )
    await state.set_state(ReviewStates.entering_comment)
    await callback.answer()


@router.message(ReviewStates.entering_comment)
async def review_enter_comment(message: Message, state: FSMContext):
    fsm_data = await state.get_data()
    booking_id = fsm_data.get("review_booking_id")
    rating = fsm_data.get("review_rating")
    comment = message.text.strip() if message.text else "-"

    result = await api_post(
        f"/api/bookings/{booking_id}/review",
        json={"rating": rating, "comment": comment},
        params={"telegram_user_id": message.from_user.id},
    )
    await state.clear()

    if result and "error" not in result:
        await message.answer("Спасибо. Отзыв сохранён и уже доступен администратору.")
    else:
        err = result.get("error", "Не удалось сохранить отзыв.") if result else "Сервер недоступен."
        await message.answer(f"❌ {err}")


# ─── Reminder loop ────────────────────────────────────────────────────────────

sent_reminders_24h: set[int] = set()


async def reminder_loop():
    """Runs every 60 seconds. Sends 24h reminders and return campaigns."""
    from app.database import SessionLocal
    from app.models import Booking, BookingStatus, Client, ReminderLog
    from app.services.telegram import send_campaign_21d, send_campaign_30d, send_campaign_60d, send_reminder_24h
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.app_timezone)

    while True:
        try:
            db = SessionLocal()
            try:
                now = datetime.now(tz).replace(tzinfo=None)
                now_date = now.date()

                # 24h reminders — only for bookings that are still ≥24h away
                target_24h = now_date + timedelta(days=1)
                bookings_24 = db.query(Booking).filter(
                    Booking.desired_date == target_24h,
                    Booking.status == BookingStatus.confirmed,
                ).all()
                for b in bookings_24:
                    booking_dt = datetime.combine(b.desired_date, b.desired_time)
                    hours_left = (booking_dt - now).total_seconds() / 3600
                    if b.id not in sent_reminders_24h and b.client.telegram_user_id and hours_left >= 24:
                        await send_reminder_24h(b)
                        sent_reminders_24h.add(b.id)

                # Return campaigns
                await _send_returning_campaigns(db, now)

            finally:
                db.close()
        except Exception as e:
            logger.warning(f"reminder_loop error: {e}")

        await asyncio.sleep(60)


async def _send_returning_campaigns(db, now: datetime):
    from app.models import Booking, BookingStatus, Client, ReminderLog
    from app.services.telegram import send_campaign_21d, send_campaign_30d, send_campaign_60d

    campaigns = [
        (timedelta(days=21), "personal_offer_21d", send_campaign_21d),
        (timedelta(days=30), "return_30d", send_campaign_30d),
        (timedelta(days=60), "inactive_60d", send_campaign_60d),
    ]

    clients = db.query(Client).filter(Client.telegram_user_id != None).all()
    for client in clients:
        # has active booking?
        active = db.query(Booking).filter(
            Booking.client_id == client.id,
            Booking.status.in_([BookingStatus.new, BookingStatus.confirmed]),
        ).first()
        if active:
            continue

        last_booking = (
            db.query(Booking)
            .filter(Booking.client_id == client.id)
            .order_by(Booking.desired_date.desc())
            .first()
        )
        if not last_booking:
            continue

        last_dt = datetime.combine(last_booking.desired_date, datetime.min.time())
        days_since = (now - last_dt).days

        for delta, reminder_type, send_fn in campaigns:
            if days_since >= delta.days:
                already_sent = db.query(ReminderLog).filter(
                    ReminderLog.client_id == client.id,
                    ReminderLog.reminder_type == reminder_type,
                ).first()
                if not already_sent:
                    await send_fn(client)
                    log = ReminderLog(client_id=client.id, reminder_type=reminder_type)
                    db.add(log)
                    db.commit()
                    break  # one campaign per cycle per client


# ─── One-shot reminders (used by the /internal/reminders cron endpoint) ──────

async def run_reminders_once():
    """
    Single pass of all reminder & return-campaign logic.
    Called from the Vercel cron endpoint every 5 minutes.
    Does NOT loop — returns after one iteration.
    """
    from app.database import SessionLocal
    from app.models import Booking, BookingStatus, Client, ReminderLog
    from app.services.telegram import (
        send_campaign_21d, send_campaign_30d, send_campaign_60d, send_reminder_24h,
    )
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.app_timezone)
    db = SessionLocal()
    try:
        now = datetime.now(tz).replace(tzinfo=None)
        now_date = now.date()

        # 24h reminders
        target_24h = now_date + timedelta(days=1)
        for b in db.query(Booking).filter(
            Booking.desired_date == target_24h,
            Booking.status == BookingStatus.confirmed,
        ).all():
            if b.client.telegram_user_id:
                log_key = f"reminder_24h_{b.id}"
                already = db.query(ReminderLog).filter(
                    ReminderLog.client_id == b.client_id,
                    ReminderLog.reminder_type == log_key,
                ).first()
                if not already:
                    await send_reminder_24h(b)
                    db.add(ReminderLog(client_id=b.client_id, reminder_type=log_key))
                    db.commit()

        # Return campaigns
        await _send_returning_campaigns(db, now)
    finally:
        db.close()


# ─── run_polling — запуск из lifespan FastAPI (Railway / локально) ────────────

async def run_polling():
    """
    Запускает бота в режиме polling внутри уже работающего event loop.
    Вызывается через asyncio.create_task() из lifespan FastAPI.
    """
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN не задан в .env")
        return

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="services", description="Программы и занятия"),
        BotCommand(command="book", description="Записаться на занятие"),
        BotCommand(command="masters", description="Преподаватели"),
        BotCommand(command="visit", description="Моя запись"),
        BotCommand(command="about", description="О школе и контакты"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ])

    # Reminder loop работает параллельно с polling
    asyncio.create_task(reminder_loop())

    logger.info("Бот запущен (polling)")
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await bot.session.close()


# ─── main() — автономный запуск (python -m app.bot.telegram_bot) ──────────────

async def main():
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
