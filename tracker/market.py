"""Сбор цен по направлениям: лучший тариф на каждую дату."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from tracker.api import ApiError, AuthError, PricesSource
from tracker.config import Config
from tracker.util import daterange, month_key, month_keys

log = logging.getLogger(__name__)

AVIASALES = "https://www.aviasales.ru"


@dataclass(frozen=True)
class Flight:
    origin: str        # аэропорт вылета
    dest: str          # аэропорт прилёта
    airline: str       # код авиакомпании
    number: str        # «SU 1120» или пусто
    depart_at: str     # местное время вылета, ISO
    duration: int | None = None

    @property
    def date(self) -> dt.date:
        return dt.date.fromisoformat(self.depart_at[:10])

    @property
    def time(self) -> str:
        return self.depart_at[11:16]


@dataclass(frozen=True)
class Fare:
    """Самый дешёвый билет из кэша: в одну сторону или туда-обратно одной бронью."""

    kind: str                       # "ow" | "rt"
    flights: tuple[Flight, ...]     # ow: один рейс; rt: туда и обратно
    price: int                      # цена билета
    bag: int                        # оценка доплаты за снаряжение
    link: str

    @property
    def total(self) -> int:
        return self.price + self.bag

    @property
    def date(self) -> dt.date:
        return self.flights[0].date


@dataclass
class Market:
    out: dict[str, dict[dt.date, Fare]] = field(default_factory=dict)    # Москва → аэропорт
    back: dict[str, dict[dt.date, Fare]] = field(default_factory=dict)   # аэропорт → Москва
    rt: dict[str, dict[tuple[dt.date, dt.date], Fare]] = field(default_factory=dict)
    requests_ok: int = 0
    requests_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def fares_count(self) -> int:
        return sum(len(v) for v in self.out.values()) + sum(len(v) for v in self.back.values()) + sum(
            len(v) for v in self.rt.values()
        )


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _link(raw: Any) -> str:
    link = str(raw or "")
    return AVIASALES + link if link.startswith("/") else link


def _flight_number(airline: str, raw: Any) -> str:
    number = str(raw or "").strip()
    return f"{airline} {number}".strip() if number else ""


def parse_one_way(item: dict, cfg: Config) -> Fare | None:
    try:
        if cfg.direct_only and int(item.get("transfers") or 0) != 0:
            return None
        price = int(round(float(item["price"])))
        depart_at = str(item["departure_at"])
        dt.date.fromisoformat(depart_at[:10])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0:
        return None
    airline = str(item.get("airline") or "").upper()
    flight = Flight(
        origin=str(item.get("origin_airport") or item.get("origin") or ""),
        dest=str(item.get("destination_airport") or item.get("destination") or ""),
        airline=airline,
        number=_flight_number(airline, item.get("flight_number")),
        depart_at=depart_at,
        duration=_int(item.get("duration_to") or item.get("duration")),
    )
    return Fare("ow", (flight,), price, cfg.baggage_fee(airline), _link(item.get("link")))


def parse_round_trip(item: dict, cfg: Config) -> Fare | None:
    try:
        if cfg.direct_only and (
            int(item.get("transfers") or 0) != 0 or int(item.get("return_transfers") or 0) != 0
        ):
            return None
        price = int(round(float(item["price"])))
        depart_at = str(item["departure_at"])
        return_at = str(item["return_at"])
        dt.date.fromisoformat(depart_at[:10])
        dt.date.fromisoformat(return_at[:10])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0:
        return None
    airline = str(item.get("airline") or "").upper()
    origin = str(item.get("origin_airport") or item.get("origin") or "")
    dest = str(item.get("destination_airport") or item.get("destination") or "")
    out = Flight(origin, dest, airline, _flight_number(airline, item.get("flight_number")), depart_at,
                 _int(item.get("duration_to")))
    back = Flight(dest, origin, airline, "", return_at, _int(item.get("duration_back")))
    return Fare("rt", (out, back), price, 2 * cfg.baggage_fee(airline), _link(item.get("link")))


def _better(new: Fare, old: Fare | None) -> bool:
    if old is None:
        return True
    return (new.total, new.price, new.flights[0].depart_at) < (old.total, old.price, old.flights[0].depart_at)


def round_trip_month_pairs(cfg: Config) -> list[tuple[str, str]]:
    """Пары месяцев «вылет — возвращение», которые нужны для поездок из настроек."""
    nights = sorted({n for r in cfg.resorts for n in r.nights})
    pairs: list[tuple[str, str]] = []
    for day in daterange(cfg.start, cfg.end):
        for n in nights:
            ret = day + dt.timedelta(days=n)
            if ret <= cfg.end:
                pair = (month_key(day), month_key(ret))
                if pair not in pairs:
                    pairs.append(pair)
    return pairs


def collect(cfg: Config, api: PricesSource) -> Market:
    market = Market()
    months = month_keys(cfg.start, cfg.end)
    pairs = round_trip_month_pairs(cfg)
    valid_nights = {n for r in cfg.resorts for n in r.nights}

    def request(origin: str, destination: str, departure: str, ret: str | None) -> list[dict]:
        try:
            items = api.fetch(origin, destination, departure, ret)
        except AuthError:
            raise
        except ApiError as exc:
            market.requests_failed += 1
            market.errors.append(f"{origin}→{destination} {departure}{'/' + ret if ret else ''}: {exc}")
            log.warning("Запрос %s→%s %s %s не удался: %s", origin, destination, departure, ret or "", exc)
            return []
        market.requests_ok += 1
        return items

    for airport in cfg.airports:
        out = market.out.setdefault(airport, {})
        back = market.back.setdefault(airport, {})
        rt = market.rt.setdefault(airport, {})
        for month in months:
            for item in request(cfg.origin, airport, month, None):
                fare = parse_one_way(item, cfg)
                if fare and cfg.start <= fare.date <= cfg.end and _better(fare, out.get(fare.date)):
                    out[fare.date] = fare
            for item in request(airport, cfg.origin, month, None):
                fare = parse_one_way(item, cfg)
                if fare and cfg.start <= fare.date <= cfg.end and _better(fare, back.get(fare.date)):
                    back[fare.date] = fare
        for dep_month, ret_month in pairs:
            for item in request(cfg.origin, airport, dep_month, ret_month):
                fare = parse_round_trip(item, cfg)
                if not fare:
                    continue
                first, last = fare.flights[0].date, fare.flights[1].date
                if not (cfg.start <= first and last <= cfg.end) or (last - first).days not in valid_nights:
                    continue
                if _better(fare, rt.get((first, last))):
                    rt[(first, last)] = fare
    return market
