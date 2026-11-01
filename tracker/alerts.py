"""Когда присылать уведомление.

По каждому курорту берётся лучшая цена среди поездок вне дат конференции.
Поводы:
  threshold — цена не выше порога из config.yaml;
  new_min   — новый минимум за всё время (дешевле прежнего хотя бы на new_min_step_percent);
  drop      — цена ниже минимума за последние сутки на drop_percent и больше.
Повторно об одном и том же не сообщаем: порог «перевзводится», только когда цена
поднимется выше него, а минимум сравнивается с последним, о котором уже писали.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from tracker.config import Config, Resort
from tracker.store import BestRow
from tracker.trips import Trip
from tracker.util import iso_utc

THRESHOLD_REARM = 1.02   # порог снова «взведён», когда цена выше него на 2%


@dataclass(frozen=True)
class Event:
    kind: str                 # "threshold" | "new_min" | "drop"
    prev: int | None = None   # с чем сравнивали
    percent: float | None = None


@dataclass
class ResortAlert:
    resort: Resort
    trip: Trip
    events: list[Event] = field(default_factory=list)

    def has(self, kind: str) -> bool:
        return any(e.kind == kind for e in self.events)


def evaluate(
    cfg: Config,
    state: dict,
    bests: dict[str, Trip | None],
    history: list[BestRow],
    now: dt.datetime,
) -> list[ResortAlert]:
    """Возвращает уведомления и обновляет state. history — строки до текущей проверки."""
    step = cfg.alerts.new_min_step_percent / 100
    drop = cfg.alerts.drop_percent / 100
    since = now - dt.timedelta(hours=24)
    resorts_state = state.setdefault("resorts", {})
    alerts: list[ResortAlert] = []

    for resort in cfg.resorts:
        trip = bests.get(resort.id)
        if trip is None or trip.total is None:
            continue
        total = trip.total
        s = resorts_state.get(resort.id)
        first_seen = s is None or s.get("min_ever") is None
        if s is None:
            s = resorts_state[resort.id] = {}
        events: list[Event] = []

        # Новый минимум за всё время
        if first_seen:
            s.update(min_ever=total, min_ever_time=iso_utc(now), min_ever_trip=trip.key, min_notified=total)
        else:
            if total < s["min_ever"]:
                s.update(min_ever=total, min_ever_time=iso_utc(now), min_ever_trip=trip.key)
            base = s.get("min_notified") or s["min_ever"]
            if total < base and total <= base * (1 - step):
                events.append(Event("new_min", prev=base))
                s["min_notified"] = total

        # Порог
        threshold = resort.threshold
        if threshold:
            notified = s.get("thr_notified")
            if total <= threshold:
                if notified is None or total <= notified * (1 - step):
                    events.append(Event("threshold", prev=threshold))
                    s["thr_notified"] = total
            elif total > threshold * THRESHOLD_REARM:
                s["thr_notified"] = None
        else:
            s["thr_notified"] = None

        # Заметное снижение относительно минимума за последние сутки
        window = [r.total for r in history if r.resort == resort.id and r.total and since <= r.time < now]
        if window and not first_seen:
            reference = min(window)
            if total <= reference * (1 - drop):
                events.append(Event("drop", prev=reference, percent=(reference - total) / reference * 100))

        if events:
            alerts.append(ResortAlert(resort, trip, events))
    return alerts


def change_24h(history: list[BestRow], resort_id: str, now: dt.datetime, current: int | None) -> int | None:
    """Изменение лучшей цены за сутки (по самой свежей проверке, которой уже больше ~суток)."""
    if current is None:
        return None
    cutoff = now - dt.timedelta(hours=23)
    previous = [r for r in history if r.resort == resort_id and r.time <= cutoff and r.total]
    if not previous:
        return None
    return current - max(previous, key=lambda r: r.time).total
