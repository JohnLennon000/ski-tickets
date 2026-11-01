"""Тексты сообщений для Telegram (HTML)."""
from __future__ import annotations

import datetime as dt
from html import escape

from tracker.alerts import ResortAlert, change_24h
from tracker.config import Config, Resort
from tracker.market import Flight
from tracker.store import BestRow
from tracker.trips import Trip
from tracker.util import MSK, airline_name, airport_name, day_label, ddmm, human_day, nights_word, num, rub

CACHE_NOTE = (
    "Цены из кэша Aviasales — их находили другие люди за последние дни. "
    "Перед покупкой откройте ссылку и проверьте живую цену."
)


def h(text: object) -> str:
    return escape(str(text), quote=False)


def link(url: str, text: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{h(text)}</a>'


def flight_label(flight: Flight) -> str:
    """«Аэрофлот SU 1120», «S7 1386», «Победа»."""
    name = airline_name(flight.airline)
    if not flight.number:
        return name
    return flight.number if name == flight.airline else f"{name} {flight.number}"


def airlines_text(trip: Trip) -> str:
    names: list[str] = []
    for flight in trip.flights:
        name = airline_name(flight.airline)
        if name not in names:
            names.append(name)
    return " / ".join(names)


def route_text(trip: Trip, resort: Resort) -> str:
    """Для курортов с несколькими аэропортами — куда именно летим."""
    if len(resort.airports) < 2 or not trip.flights:
        return ""
    there, back = trip.flights[0].dest, trip.flights[1].origin
    if there == back:
        return airport_name(there)
    return f"{airport_name(there)} / {airport_name(back)}"


def trip_line_plain(trip: Trip, resort: Resort) -> str:
    parts = [f"{day_label(trip.depart)} → {day_label(trip.ret)}, {nights_word(trip.nights)}"]
    route = route_text(trip, resort)
    if route:
        parts.append(route)
    parts.append(airlines_text(trip))
    return " · ".join(parts)


def trip_line(trip: Trip, resort: Resort) -> str:
    return h(trip_line_plain(trip, resort))


def leg_lines(trip: Trip) -> list[str]:
    if trip.rt:
        out, back = trip.rt.flights
        return [
            h(f"Туда: {day_label(out.date)} {out.time} {out.origin}→{out.dest}, {flight_label(out)}"),
            h(f"Обратно: {day_label(back.date)} {back.time} {back.origin}→{back.dest}"),
            h(f"Одна бронь туда-обратно: {rub(trip.rt.price)} + багаж ≈{num(trip.rt.bag)}"),
        ]
    lines = []
    for label, fare in (("Туда", trip.out), ("Обратно", trip.back)):
        if fare is None:
            continue
        f = fare.flights[0]
        lines.append(
            h(
                f"{label}: {day_label(f.date)} {f.time} {f.origin}→{f.dest}, {flight_label(f)} — "
                f"{rub(fare.price)} + багаж ≈{num(fare.bag)}"
            )
        )
    return lines


def reasons_plain(alert: ResortAlert) -> str:
    texts = []
    for event in alert.events:
        if event.kind == "threshold":
            texts.append(f"не дороже порога {rub(event.prev)}")
        elif event.kind == "new_min":
            texts.append(f"новый минимум, было {rub(event.prev)}")
        elif event.kind == "drop":
            texts.append(f"−{event.percent:.0f}% за сутки, было {rub(event.prev)}")
    text = "; ".join(texts)
    return text[:1].upper() + text[1:]


def alert_icon(alert: ResortAlert) -> str:
    return "🔔" if alert.has("threshold") else "📉"


def reasons_text(alert: ResortAlert) -> str:
    return f"{alert_icon(alert)} {h(reasons_plain(alert))}"


def alert_message(alerts: list[ResortAlert], cfg: Config, site_url: str | None) -> str:
    blocks = []
    for alert in alerts:
        trip = alert.trip
        lines = [
            f"<b>{h(alert.resort.name)} — {rub(trip.total)}</b>",
            reasons_text(alert),
            trip_line(trip, alert.resort),
            *leg_lines(trip),
            link(trip.search_link(cfg.origin), "Проверить на Aviasales"),
        ]
        blocks.append("\n".join(lines))
    footer = f"<i>{h(CACHE_NOTE)}</i>"
    if site_url:
        footer += "\n" + link(site_url, "Графики и все даты")
    return "\n\n".join(blocks + [footer])


def _change_text(change: int | None) -> str:
    if change is None:
        return ""
    if change < 0:
        return f" ↓{num(-change)} за сутки"
    if change > 0:
        return f" ↑{num(change)} за сутки"
    return " · за сутки без изменений"


def resort_digest_block(
    resort: Resort,
    trip: Trip | None,
    state: dict,
    history: list[BestRow],
    now: dt.datetime,
    cfg: Config,
    alert: ResortAlert | None = None,
) -> str:
    if trip is None:
        return f"<b>{h(resort.name)}</b> — нет цен на прямые рейсы в эти даты"
    s = state.get("resorts", {}).get(resort.id, {})
    lines = [f"<b>{h(resort.name)}</b> — {rub(trip.total)}{_change_text(change_24h(history, resort.id, now, trip.total))}"]
    if alert:
        lines.append(reasons_text(alert))
    lines.append(trip_line(trip, resort))
    if alert:
        lines.extend(leg_lines(trip))
    extra = []
    if s.get("min_ever") and s["min_ever"] < trip.total:
        extra.append(f"минимум был {num(s['min_ever'])}")
    if resort.threshold:
        extra.append(f"порог {num(resort.threshold)}" + (" ✓" if trip.total <= resort.threshold else ""))
    if extra:
        lines.append(h(" · ".join(extra)))
    lines.append(link(trip.search_link(cfg.origin), "Проверить на Aviasales"))
    return "\n".join(lines)


def digest_message(
    cfg: Config,
    bests: dict[str, Trip | None],
    state: dict,
    history: list[BestRow],
    now: dt.datetime,
    site_url: str | None,
    edit_url: str | None,
    *,
    welcome: bool = False,
    alerts: list[ResortAlert] | None = None,
) -> str:
    local = now.astimezone(MSK)
    if welcome:
        head = [
            "✅ <b>Слежу за ценами на билеты</b>",
            h(
                "Пришлю уведомление, когда лучшая цена по курорту станет новым минимумом, "
                f"опустится до порога или упадёт на {cfg.alerts.drop_percent:g}% за сутки. "
                f"Сводка — каждое утро в {cfg.alerts.digest_hour}:00 по Москве, ночью сообщения без звука."
            ),
            "",
            f"<b>Лучшие цены сейчас</b> ({local:%H:%M} МСК)",
        ]
    else:
        head = [f"☀️ <b>Цены на {h(human_day(local.date()))}</b>"]
    head.append("Туда-обратно на 1 взрослого, прямые рейсы, с багажом для лыж")

    by_resort = {a.resort.id: a for a in alerts or []}
    blocks = [
        resort_digest_block(r, bests.get(r.id), state, history, now, cfg, by_resort.get(r.id))
        for r in cfg.resorts
    ]

    notes = []
    for period in cfg.low_priority:
        label = f" ({period.label})" if period.label else ""
        notes.append(h(f"Поездки, которые задевают {ddmm(period.start)}–{ddmm(period.end)}{label}, не учитываются."))
    if not any(r.threshold for r in cfg.resorts):
        notes.append("Пороги цен пока не заданы" + (": " + link(edit_url, "задать в настройках") if edit_url else "."))
    if by_resort:
        notes.append(f"<i>{h(CACHE_NOTE)}</i>")
    if site_url:
        notes.append(link(site_url, "Графики и все даты"))
    return "\n".join(head) + "\n\n" + "\n\n".join(blocks) + "\n\n" + "\n".join(notes)


def problem_message(text: str) -> str:
    return f"⚠️ <b>Не получилось проверить цены</b>\n{h(text)}"


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Telegram принимает до 4096 символов; режем по пустым строкам, не ломая теги."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = block[:limit]
    if current:
        parts.append(current)
    return parts


# --- пуш-уведомления в приложение: коротко и без разметки --------------------------

def push_alerts(alerts: list[ResortAlert], silent: bool = False) -> list[dict]:
    return [
        {
            "title": f"{alert_icon(a)} {a.resort.name} — {rub(a.trip.total)}",
            "body": f"{reasons_plain(a)}\n{trip_line_plain(a.trip, a.resort)}",
            "url": f"./#{a.resort.id}/{a.trip.key}",
            "tag": f"alert-{a.resort.id}",
            "silent": silent,
        }
        for a in alerts
    ]


def push_digest(
    cfg: Config,
    bests: dict[str, Trip | None],
    history: list[BestRow],
    now: dt.datetime,
    *,
    alerts: list[ResortAlert] | None = None,
    welcome: bool = False,
    silent: bool = False,
) -> dict:
    marked = {a.resort.id: alert_icon(a) for a in alerts or []}
    lines = []
    for resort in cfg.resorts:
        trip = bests.get(resort.id)
        if trip is None:
            lines.append(f"{resort.name}: нет цен")
            continue
        change = change_24h(history, resort.id, now, trip.total)
        delta = f" ↓{num(-change)}" if change and change < 0 else f" ↑{num(change)}" if change else ""
        mark = f" {marked[resort.id]}" if resort.id in marked else ""
        lines.append(f"{resort.name} — {rub(trip.total)}{delta}{mark}")
    title = "✅ Уведомления включены" if welcome else f"☀️ Цены на {human_day(now.astimezone(MSK).date())}"
    return {"title": title, "body": "\n".join(lines), "url": "./", "tag": "digest", "silent": silent}


def push_problem(text: str) -> dict:
    return {"title": "⚠️ Не получилось проверить цены", "body": text[:300], "url": "./", "tag": "problem"}
