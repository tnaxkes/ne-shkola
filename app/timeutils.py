from datetime import datetime, date, time
from zoneinfo import ZoneInfo

from app.config import settings

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

WEEKDAYS_RU = {
    0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс",
}


def tz() -> ZoneInfo:
    return ZoneInfo(settings.app_timezone)


def now_local() -> datetime:
    return datetime.now(tz())


def today_local() -> date:
    return now_local().date()


def format_date_ru(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month]} {d.year}"


def format_date_ru_short(d: date) -> str:
    wd = WEEKDAYS_RU[d.weekday()]
    return f"{d.day:02d}.{d.month:02d} ({wd})"


def format_time(t: time) -> str:
    return t.strftime("%H:%M")


def format_datetime_ru(dt: datetime) -> str:
    if dt is None:
        return ""
    d = dt.date()
    return f"{d.day} {MONTHS_RU[d.month]} {d.year}, {dt.strftime('%H:%M')}"


def parse_workday_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))
