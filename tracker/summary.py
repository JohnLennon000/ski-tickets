"""docs/data/summary.json — текущие цены для сайта."""
from __future__ import annotations

import datetime as dt

from tracker.alerts import change_24h
from tracker.config import Config
from tracker.market import Fare, Flight, Market
from tracker.store import BestRow
from tracker.trips import Trip
from tracker.util import AIRLINES, AIRPORTS, iso_utc


def _leg(flight: Flight, direction: str, fare: Fare | None) -> dict:
    return {
        "dir": direction,
        "date": flight.date.isoformat(),
        "time": flight.time,
        "from": flight.origin,
        "to": flight.dest,
        "airline": flight.airline,
        "flight": flight.number,
        "price": fare.price if fare else None,
        "bag": fare.bag if fare else None,
        "link": fare.link if fare else None,
        "found": fare.found.isoformat() if fare and fare.found else None,
        "luggage": fare.luggage if fare else None,
    }


def trip_json(trip: Trip, cfg: Config) -> dict:
    data: dict = {
        "key": trip.key,
        "depart": trip.depart.isoformat(),
        "return": trip.ret.isoformat(),
        "nights": trip.nights,
        "low": (trip.low.label or "низкий приоритет") if trip.low else None,
        "total": trip.total,
    }
    if not trip.priced:
        return data
    if trip.rt:
        out, back = trip.rt.flights
        legs = [_leg(out, "out", None), _leg(back, "back", None)]
    else:
        legs = [_leg(trip.out.flights[0], "out", trip.out), _leg(trip.back.flights[0], "back", trip.back)]
    fares = [trip.rt] if trip.rt else [trip.out, trip.back]
    found = [f.found for f in fares if f and f.found]
    luggage = [f.luggage for f in fares if f and f.luggage is not None]
    data.update(
        found=min(found).isoformat() if found else None,      # самая старая из цен поездки
        luggage=min(luggage) if luggage and len(luggage) == len(fares) else None,
        price=trip.price,
        bag=trip.bag,
        kind="rt" if trip.rt else "legs",
        link=trip.search_link(cfg.origin),
        legs=legs,
    )
    return data


def build_summary(
    cfg: Config,
    trips: dict[str, list[Trip]],
    bests: dict[str, Trip | None],
    state: dict,
    history: list[BestRow],
    now: dt.datetime,
    market: Market,
    repo: str | None,
    push: dict | None = None,
    checks: list | None = None,
) -> dict:
    from tracker.checks import corrections

    checks = checks or []
    fixes = corrections(checks)
    resorts = []
    for resort in cfg.resorts:
        s = state.get("resorts", {}).get(resort.id, {})
        best = bests.get(resort.id)
        resorts.append(
            {
                "id": resort.id,
                "name": resort.name,
                "airports": list(resort.airports),
                "nights": list(resort.nights),
                "threshold": resort.threshold,
                "best": best.key if best else None,
                "min_ever": {
                    "total": s["min_ever"],
                    "time": s.get("min_ever_time"),
                    "key": s.get("min_ever_trip"),
                }
                if s.get("min_ever")
                else None,
                "change_24h": change_24h(history, resort.id, now, best.total if best else None),
                "trips": [trip_json(t, cfg) for t in trips.get(resort.id, [])],
                "correction": fixes.get(resort.id),
                "checks": [
                    {"trip": c.trip, "price": c.price, "app_total": c.app_total, "seen_at": iso_utc(c.seen_at)}
                    for c in sorted(checks, key=lambda c: c.seen_at, reverse=True)
                    if c.resort == resort.id
                ][:20],
            }
        )
    return {
        "updated": iso_utc(now),
        "origin": cfg.origin,
        "period": {"start": cfg.start.isoformat(), "end": cfg.end.isoformat()},
        "low_priority": [
            {"from": p.start.isoformat(), "to": p.end.isoformat(), "label": p.label} for p in cfg.low_priority
        ],
        "baggage": {"default": cfg.baggage_default, "airlines": cfg.baggage_airlines},
        "alerts": {"drop_percent": cfg.alerts.drop_percent, "digest_hour": cfg.alerts.digest_hour},
        "names": {"airlines": AIRLINES, "airports": AIRPORTS},
        "stats": {
            "requests_ok": market.requests_ok,
            "requests_failed": market.requests_failed,
            "fares": market.fares_count(),
        },
        "repo": repo,
        "push": push,
        "resorts": resorts,
    }
