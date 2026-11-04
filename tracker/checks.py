"""Цены, которые пользователь сам увидел на Aviasales («Я проверил цену»).

Приложение открывает GitHub с готовым файлом checks/<курорт>-<время>.json, пользователь
нажимает «Commit changes». Здесь эти файлы читаются, и по ним считается поправка:
насколько реальная цена с багажом обычно отличается от расчётной по курорту.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import statistics
from dataclasses import dataclass
from pathlib import Path

from tracker.util import parse_utc

log = logging.getLogger(__name__)

KEEP_DAYS = 14          # поправка — по проверкам за последние две недели
MAX_DELTA = 0.5         # проверка, отличающаяся от расчёта больше чем на 50%, скорее всего опечатка


@dataclass(frozen=True)
class Check:
    resort: str
    trip: str           # ключ поездки «2027-03-03/8»
    price: int          # цена, которую пользователь увидел (с багажом)
    app_total: int      # сколько показывало приложение в тот момент
    seen_at: dt.datetime

    @property
    def delta(self) -> int:
        return self.price - self.app_total


def load_checks(root: Path, now: dt.datetime) -> list[Check]:
    directory = Path(root) / "checks"
    if not directory.is_dir():
        return []
    since = now - dt.timedelta(days=KEEP_DAYS)
    result = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            check = Check(
                resort=str(raw["resort"]),
                trip=str(raw["trip"]),
                price=int(raw["price"]),
                app_total=int(raw["app_total"]),
                seen_at=parse_utc(str(raw["seen_at"])),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Проверка %s не читается: %s", path.name, exc)
            continue
        if check.seen_at < since or check.price <= 0 or check.app_total <= 0:
            continue
        if abs(check.delta) > check.app_total * MAX_DELTA:
            log.warning("Проверка %s отличается от расчёта больше чем на половину — пропускаю", path.name)
            continue
        result.append(check)
    return result


def corrections(checks: list[Check]) -> dict[str, int]:
    """Поправка по курорту — медиана разниц «увидел − показывало приложение»."""
    by_resort: dict[str, list[int]] = {}
    for check in checks:
        by_resort.setdefault(check.resort, []).append(check.delta)
    return {rid: int(round(statistics.median(deltas) / 10) * 10) for rid, deltas in by_resort.items()}
