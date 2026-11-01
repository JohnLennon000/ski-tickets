"""Синтетические ответы API для тестов (цены выдуманные, только для проверки кода)."""
from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

from tracker.api import fixture_name
from tracker.config import Config
from tracker.market import round_trip_month_pairs
from tracker.util import daterange, month_keys

BASE = {"AER": 7000, "MRV": 6200, "NAL": 6800, "NOZ": 11000, "KEJ": 10500,
        "KVK": 9000, "MMK": 7200, "RGK": 12500, "UUS": 26000}
CARRIERS = {"AER": ["DP", "SU", "S7", "U6"], "MRV": ["DP", "SU", "UT"], "NAL": ["SU", "UT"],
            "NOZ": ["SU", "S7", "DP"], "KEJ": ["SU", "S7"], "KVK": ["SU"], "MMK": ["SU", "DP", "S7"],
            "RGK": ["SU", "S7"], "UUS": ["SU"]}
MOSCOW = ["SVO", "VKO", "DME"]


def _item(origin_airport, dest_airport, origin, dest, airline, day, hour, price, rng, ret=None):
    item = {
        "origin": origin, "destination": dest,
        "origin_airport": origin_airport, "destination_airport": dest_airport,
        "price": price, "airline": airline, "flight_number": str(rng.randint(100, 1999)),
        "departure_at": f"{day.isoformat()}T{hour:02d}:{rng.choice([0, 15, 30, 45]):02d}:00+03:00",
        "transfers": 0, "duration": 180, "duration_to": 180,
        "link": f"/search/{origin}{day:%d%m}{dest}1?t={airline}{rng.randint(10**9, 10**10)}",
    }
    if ret:
        item.update(return_at=f"{ret.isoformat()}T{rng.randint(8, 21):02d}:10:00+03:00", return_transfers=0,
                    duration_back=180)
        item["link"] = f"/search/{origin}{day:%d%m}{dest}{ret:%d%m}1?t={airline}{rng.randint(10**9, 10**10)}"
    return item


def write_fixtures(cfg: Config, directory: Path, *, seed: int = 1, shift: float = 1.0) -> None:
    """shift < 1 — всё подешевело (для проверки уведомлений)."""
    rng = random.Random(seed)
    directory.mkdir(parents=True, exist_ok=True)
    for airport in cfg.airports:
        base = BASE.get(airport, 9000)
        carriers = CARRIERS.get(airport, ["SU"])
        for month in month_keys(cfg.start, cfg.end):
            first = dt.date.fromisoformat(month + "-01")
            last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            for direction in ("out", "back"):
                items = []
                for day in daterange(first, last):
                    if rng.random() < 0.12:  # в кэше бывают дыры
                        continue
                    season = 1.25 if dt.date(day.year, 2, 20) <= day <= dt.date(day.year, 3, 9) else 1.0
                    weekend = 1.12 if day.weekday() in (4, 5, 6) else 1.0
                    for _ in range(rng.randint(1, 3)):
                        price = int(base * season * weekend * rng.uniform(0.85, 1.3) * shift) // 10 * 10
                        airline = rng.choice(carriers)
                        if direction == "out":
                            items.append(_item(rng.choice(MOSCOW), airport, "MOW", airport, airline, day,
                                               rng.randint(6, 22), price, rng))
                        else:
                            items.append(_item(airport, rng.choice(MOSCOW), airport, "MOW", airline, day,
                                               rng.randint(6, 22), price, rng))
                    if rng.random() < 0.05:  # с пересадкой — должен отбрасываться
                        bad = _item("SVO", airport, "MOW", airport, "SU", day, 9, int(base * 0.5), rng)
                        bad["transfers"] = 1
                        items.append(bad)
                origin, dest = ("MOW", airport) if direction == "out" else (airport, "MOW")
                (directory / fixture_name(origin, dest, month, None)).write_text(json.dumps(items), encoding="utf-8")
        for dep_month, ret_month in round_trip_month_pairs(cfg):
            items = []
            for day in daterange(cfg.start, cfg.end):
                if f"{day:%Y-%m}" != dep_month or rng.random() < 0.7:
                    continue
                for n in (7, 8):
                    ret = day + dt.timedelta(days=n)
                    if f"{ret:%Y-%m}" != ret_month:
                        continue
                    price = int(base * 2 * rng.uniform(0.85, 1.25) * shift) // 10 * 10
                    items.append(_item("SVO", airport, "MOW", airport, rng.choice(carriers), day, 9, price, rng, ret))
            (directory / fixture_name("MOW", airport, dep_month, ret_month)).write_text(json.dumps(items),
                                                                                         encoding="utf-8")
