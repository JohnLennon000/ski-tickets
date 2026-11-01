"""Форматирование дат, сумм и справочники."""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

MSK = dt.timezone(dt.timedelta(hours=3))
NBSP = " "

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
MONTHS_SHORT = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

AIRLINES = {
    "SU": "Аэрофлот",
    "FV": "Россия",
    "HZ": "Аврора",
    "DP": "Победа",
    "S7": "S7",
    "GH": "Глобус",
    "U6": "Уральские авиалинии",
    "UT": "ЮТэйр",
    "5N": "Smartavia",
    "WZ": "Red Wings",
    "A4": "Азимут",
    "N4": "Nordwind",
    "YC": "Ямал",
    "IO": "ИрАэро",
    "R3": "Якутия",
    "7R": "РусЛайн",
    "D2": "Северсталь",
    "Y7": "NordStar",
    "6R": "Алроса",
    "KV": "КрасАвиа",
    "EO": "Икар",
}

AIRPORTS = {
    "MOW": "Москва",
    "SVO": "Шереметьево",
    "DME": "Домодедово",
    "VKO": "Внуково",
    "ZIA": "Жуковский",
    "AER": "Сочи",
    "MRV": "Минеральные Воды",
    "NAL": "Нальчик",
    "NOZ": "Новокузнецк",
    "KEJ": "Кемерово",
    "KVK": "Апатиты",
    "MMK": "Мурманск",
    "RGK": "Горно-Алтайск",
    "BAX": "Барнаул",
    "UUS": "Южно-Сахалинск",
    "OGZ": "Владикавказ",
    "IGT": "Магас",
}


def airline_name(code: str) -> str:
    return AIRLINES.get(code, code or "?")


def airport_name(code: str) -> str:
    return AIRPORTS.get(code, code)


def num(value: int | float) -> str:
    return f"{int(round(value)):,}".replace(",", NBSP)


def rub(value: int | float) -> str:
    return f"{num(value)}{NBSP}₽"


def ddmm(day: dt.date) -> str:
    return day.strftime("%d.%m")


def day_label(day: dt.date) -> str:
    """«пн 22.02»"""
    return f"{WEEKDAYS[day.weekday()]} {ddmm(day)}"


def human_day(day: dt.date) -> str:
    """«1 ноября»"""
    return f"{day.day} {MONTHS_GEN[day.month - 1]}"


def nights_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} ночь"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} ночи"
    return f"{n} ночей"


def daterange(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)


def month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_keys(start: dt.date, end: dt.date) -> list[str]:
    keys = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return keys


def iso_utc(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def parse_utc(text: str) -> dt.datetime:
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = dt.datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)
