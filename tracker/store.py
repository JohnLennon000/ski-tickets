"""Хранение истории цен (CSV в docs/data — их же читает сайт) и состояния."""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from tracker.trips import Trip
from tracker.util import iso_utc, parse_utc

log = logging.getLogger(__name__)

BEST_FIELDS = ["time", "resort", "total", "depart", "nights"]
TRIP_FIELDS = ["time", "depart", "nights", "total", "price", "bag", "airports", "airlines"]
_MISSING = object()


@dataclass(frozen=True)
class BestRow:
    time: dt.datetime
    resort: str
    total: int | None
    depart: str
    nights: int | None


def _opt_int(text: str | None) -> int | None:
    try:
        return int(text) if text not in (None, "") else None
    except ValueError:
        return None


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.data_dir = self.root / "docs" / "data"
        self.trips_dir = self.data_dir / "trips"
        self.best_path = self.data_dir / "best.csv"
        self.summary_path = self.data_dir / "summary.json"
        self.state_path = self.root / "state" / "state.json"

    # --- состояние уведомлений -------------------------------------------------
    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            log.warning("state.json повреждён — начинаю заново")
            return {}

    def save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )

    # --- лучшая цена по курортам на каждую проверку ----------------------------
    def read_best(self) -> list[BestRow]:
        if not self.best_path.exists():
            return []
        rows: list[BestRow] = []
        with self.best_path.open(encoding="utf-8", newline="") as fh:
            for rec in csv.DictReader(fh):
                try:
                    rows.append(
                        BestRow(
                            time=parse_utc(rec["time"]),
                            resort=rec["resort"],
                            total=_opt_int(rec.get("total")),
                            depart=rec.get("depart") or "",
                            nights=_opt_int(rec.get("nights")),
                        )
                    )
                except (KeyError, ValueError):
                    continue
        return rows

    def append_best(self, now: dt.datetime, bests: dict[str, Trip | None]) -> None:
        self._append(
            self.best_path,
            BEST_FIELDS,
            [
                {
                    "time": iso_utc(now),
                    "resort": rid,
                    "total": trip.total if trip else "",
                    "depart": trip.depart.isoformat() if trip else "",
                    "nights": trip.nights if trip else "",
                }
                for rid, trip in bests.items()
            ],
        )

    # --- история каждой поездки: строка пишется, только когда цена изменилась ---
    def append_trip_changes(self, now: dt.datetime, trips: dict[str, list[Trip]], state: dict) -> int:
        last_all = state.setdefault("last_totals", {})
        written = 0
        for rid, items in trips.items():
            last = last_all.setdefault(rid, {})
            rows = []
            for trip in items:
                total = trip.total
                previous = last.get(trip.key, _MISSING)
                if previous is _MISSING and total is None:
                    continue
                if previous == total:
                    continue
                last[trip.key] = total
                rows.append(
                    {
                        "time": iso_utc(now),
                        "depart": trip.depart.isoformat(),
                        "nights": trip.nights,
                        "total": total if total is not None else "",
                        "price": trip.price if total is not None else "",
                        "bag": trip.bag if total is not None else "",
                        "airports": trip.airports,
                        "airlines": trip.airlines,
                    }
                )
            current = {t.key for t in items}
            for key in [k for k in last if k not in current]:
                del last[key]
            if rows:
                self._append(self.trips_dir / f"{rid}.csv", TRIP_FIELDS, rows)
                written += len(rows)
        return written

    def write_summary(self, summary: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

    @staticmethod
    def _append(path: Path, fields: list[str], rows: list[dict]) -> None:
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            if new:
                writer.writeheader()
            writer.writerows(rows)
