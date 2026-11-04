"""Чтение и проверка config.yaml."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Ошибка в config.yaml. Текст показывается пользователю как есть."""


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date
    label: str = ""

    def overlaps(self, first: dt.date, last: dt.date) -> bool:
        return first <= self.end and last >= self.start


@dataclass(frozen=True)
class Resort:
    id: str
    name: str
    airports: tuple[str, ...]
    nights: tuple[int, ...]
    threshold: int | None = None


@dataclass(frozen=True)
class AlertSettings:
    new_min_step_percent: float = 1.0
    drop_percent: float = 5.0
    digest_hour: int = 8
    quiet_from: int = 23
    quiet_to: int = 8

    def is_quiet(self, hour: int) -> bool:
        if self.quiet_from == self.quiet_to:
            return False
        if self.quiet_from < self.quiet_to:
            return self.quiet_from <= hour < self.quiet_to
        return hour >= self.quiet_from or hour < self.quiet_to


@dataclass(frozen=True)
class Config:
    origin: str
    start: dt.date
    end: dt.date
    nights: tuple[int, ...]
    low_priority: tuple[Period, ...]
    direct_only: bool
    resorts: tuple[Resort, ...]
    baggage_default: int
    baggage_airlines: dict[str, int]
    alerts: AlertSettings
    night_returns: bool = False   # учитывать ли обратные рейсы с вылетом 0:00–6:00
    max_price_age_days: int = 3   # цены старше стольких дней (по дате находки в Aviasales) не учитываются

    def baggage_fee(self, airline: str) -> int:
        return self.baggage_airlines.get((airline or "").upper(), self.baggage_default)

    def low_priority_for(self, first: dt.date, last: dt.date) -> Period | None:
        for period in self.low_priority:
            if period.overlaps(first, last):
                return period
        return None

    @property
    def airports(self) -> list[str]:
        seen: list[str] = []
        for resort in self.resorts:
            for code in resort.airports:
                if code not in seen:
                    seen.append(code)
        return seen


_IATA = re.compile(r"[A-Z]{3}")
_ID = re.compile(r"[a-z0-9_-]{1,40}")
_MONEY = re.compile(r"\s*(\d[\d ]*)\s*(?:₽|руб\.?|р\.?)?\s*", re.IGNORECASE)


def _date(value: Any, where: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                return dt.datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
    raise ConfigError(f"{where}: не понимаю дату «{value}». Пишите так: 2027-02-08 или 08.02.2027")


def _money(value: Any, where: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ConfigError(f"{where}: ожидается сумма в рублях, например 16000")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ConfigError(f"{where}: сумма не может быть отрицательной")
        return int(value)
    if isinstance(value, str):
        match = _MONEY.fullmatch(value.replace(" ", " "))
        if match:
            return int(match.group(1).replace(" ", ""))
    raise ConfigError(f"{where}: не понимаю сумму «{value}». Пишите числом, например 16000")


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: ожидается число, а указано «{value}»")
    if value < 0:
        raise ConfigError(f"{where}: число не может быть отрицательным")
    return float(value)


def _hour(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
        raise ConfigError(f"{where}: ожидается час от 0 до 23, а указано «{value}»")
    return value


def _iata(value: Any, where: str) -> str:
    code = str(value or "").strip().upper()
    if not _IATA.fullmatch(code):
        raise ConfigError(f"{where}: «{value}» не похоже на код аэропорта (три латинские буквы, например AER)")
    return code


def _nights(value: Any, where: str) -> tuple[int, ...]:
    items = value if isinstance(value, list) else [value]
    result = set()
    for item in items:
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 30:
            raise ConfigError(f"{where}: ожидается число ночей от 1 до 30, а указано «{item}»")
        result.add(item)
    if not result:
        raise ConfigError(f"{where}: не указано число ночей")
    return tuple(sorted(result))


def _period(item: Any, where: str) -> Period:
    if not isinstance(item, dict):
        raise ConfigError(f"{where}: ожидается from: и to:")
    start = _date(item.get("from"), f"{where}.from")
    end = _date(item.get("to"), f"{where}.to")
    if end < start:
        raise ConfigError(f"{where}: дата to раньше from")
    return Period(start, end, str(item.get("label") or "").strip())


def _resort(item: Any, where: str, default_nights: tuple[int, ...]) -> Resort:
    if not isinstance(item, dict):
        raise ConfigError(f"{where}: ожидается описание курорта (id, name, airports)")
    rid = str(item.get("id") or "").strip()
    if not _ID.fullmatch(rid):
        raise ConfigError(f"{where}: id должен состоять из маленьких латинских букв, цифр, «-» или «_»")
    name = str(item.get("name") or rid).strip()
    airports = item.get("airports")
    if isinstance(airports, str):
        airports = [airports]
    if not airports:
        raise ConfigError(f"Курорт «{name}»: не указаны аэропорты (airports)")
    codes = tuple(dict.fromkeys(_iata(a, f"Курорт «{name}», airports") for a in airports))
    nights = _nights(item["nights"], f"Курорт «{name}», nights") if item.get("nights") else default_nights
    threshold = _money(item.get("threshold"), f"Курорт «{name}», threshold")
    return Resort(rid, name, codes, nights, threshold)


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml не читается — проверьте отступы и двоеточия.\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml: ожидаются настройки вида «ключ: значение»")

    origin = _iata(raw.get("origin", "MOW"), "origin")
    period = raw.get("period") or {}
    start = _date(period.get("start"), "period.start")
    end = _date(period.get("end"), "period.end")
    if end <= start:
        raise ConfigError("period: дата end должна быть позже start")
    nights = _nights(raw.get("nights", [7, 8]), "nights")
    low = tuple(_period(item, f"low_priority[{i + 1}]") for i, item in enumerate(raw.get("low_priority") or []))

    resorts: list[Resort] = []
    for i, item in enumerate(raw.get("resorts") or []):
        resort = _resort(item, f"resorts[{i + 1}]", nights)
        if any(r.id == resort.id for r in resorts):
            raise ConfigError(f"Курорт «{resort.name}»: id «{resort.id}» уже занят другим курортом")
        resorts.append(resort)
    if not resorts:
        raise ConfigError("config.yaml: не задано ни одного курорта (resorts)")

    baggage = raw.get("baggage") or {}
    default_fee = _money(baggage.get("default", 0), "baggage.default") or 0
    fees: dict[str, int] = {}
    for code, value in (baggage.get("airlines") or {}).items():
        fees[str(code).strip().upper()] = _money(value, f"baggage.airlines.{code}") or 0

    alerts_raw = raw.get("alerts") or {}
    quiet = alerts_raw.get("quiet_hours", [23, 8])
    if not isinstance(quiet, list) or len(quiet) != 2:
        raise ConfigError("alerts.quiet_hours: ожидается пара часов, например [23, 8]")
    alerts = AlertSettings(
        new_min_step_percent=_number(alerts_raw.get("new_min_step_percent", 1), "alerts.new_min_step_percent"),
        drop_percent=_number(alerts_raw.get("drop_percent", 5), "alerts.drop_percent"),
        digest_hour=_hour(alerts_raw.get("digest_hour", 8), "alerts.digest_hour"),
        quiet_from=_hour(quiet[0], "alerts.quiet_hours"),
        quiet_to=_hour(quiet[1], "alerts.quiet_hours"),
    )

    return Config(
        origin=origin,
        start=start,
        end=end,
        nights=nights,
        low_priority=low,
        direct_only=bool(raw.get("direct_only", True)),
        night_returns=bool(raw.get("night_returns", False)),
        max_price_age_days=int(raw.get("max_price_age_days", 3) or 0),
        resorts=tuple(resorts),
        baggage_default=default_fee,
        baggage_airlines=fees,
        alerts=alerts,
    )
