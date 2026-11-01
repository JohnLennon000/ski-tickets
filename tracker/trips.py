"""Поездки туда-обратно из лучших цен по датам."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tracker.config import Config, Period
from tracker.market import AVIASALES, Fare, Market
from tracker.util import daterange


@dataclass
class Trip:
    resort: str
    depart: dt.date
    ret: dt.date
    nights: int
    low: Period | None = None       # задевает даты конференции — без уведомлений
    out: Fare | None = None         # билет туда (в одну сторону)
    back: Fare | None = None        # билет обратно (в одну сторону)
    rt: Fare | None = None          # или один билет туда-обратно

    @property
    def key(self) -> str:
        return f"{self.depart.isoformat()}/{self.nights}"

    @property
    def priced(self) -> bool:
        return self.rt is not None or (self.out is not None and self.back is not None)

    @property
    def eligible(self) -> bool:
        return self.low is None

    @property
    def price(self) -> int | None:
        if self.rt:
            return self.rt.price
        if self.out and self.back:
            return self.out.price + self.back.price
        return None

    @property
    def bag(self) -> int | None:
        if self.rt:
            return self.rt.bag
        if self.out and self.back:
            return self.out.bag + self.back.bag
        return None

    @property
    def total(self) -> int | None:
        if self.rt:
            return self.rt.total
        if self.out and self.back:
            return self.out.total + self.back.total
        return None

    @property
    def flights(self):
        if self.rt:
            return self.rt.flights
        if self.out and self.back:
            return (self.out.flights[0], self.back.flights[0])
        return ()

    @property
    def airports(self) -> str:
        flights = self.flights
        return f"{flights[0].dest}/{flights[1].origin}" if flights else ""

    @property
    def airlines(self) -> str:
        if self.rt:
            return self.rt.flights[0].airline
        flights = self.flights
        if not flights:
            return ""
        codes = [f.airline for f in flights]
        return codes[0] if codes[0] == codes[1] else "/".join(codes)

    def search_link(self, origin: str) -> str:
        """Ссылка на поиск Aviasales по этим датам, чтобы увидеть живую цену."""
        flights = self.flights
        dest = flights[0].dest if flights else ""
        back_from = flights[1].origin if flights else ""
        if dest and dest == back_from:
            return f"{AVIASALES}/search/{origin}{self.depart:%d%m}{dest}{self.ret:%d%m}1"
        if self.out:
            return self.out.link or f"{AVIASALES}/search/{origin}{self.depart:%d%m}{dest}1"
        return ""


def build_trips(cfg: Config, market: Market) -> dict[str, list[Trip]]:
    result: dict[str, list[Trip]] = {}
    for resort in cfg.resorts:
        trips: list[Trip] = []
        for depart in daterange(cfg.start, cfg.end):
            for nights in resort.nights:
                ret = depart + dt.timedelta(days=nights)
                if ret > cfg.end:
                    continue
                trip = Trip(resort.id, depart, ret, nights, cfg.low_priority_for(depart, ret))
                best_total: int | None = None
                for airport in resort.airports:
                    out = market.out.get(airport, {}).get(depart)
                    if out:
                        # Можно прилететь в один аэропорт группы, а улететь из другого.
                        for back_airport in resort.airports:
                            back = market.back.get(back_airport, {}).get(ret)
                            if back and (best_total is None or out.total + back.total < best_total):
                                best_total = out.total + back.total
                                trip.out, trip.back, trip.rt = out, back, None
                    rt = market.rt.get(airport, {}).get((depart, ret))
                    if rt and (best_total is None or rt.total < best_total):
                        best_total = rt.total
                        trip.out, trip.back, trip.rt = None, None, rt
                trips.append(trip)
        result[resort.id] = trips
    return result


def best_trip(trips: list[Trip]) -> Trip | None:
    candidates = [t for t in trips if t.eligible and t.priced]
    if not candidates:
        return None
    return min(candidates, key=lambda t: (t.total, t.depart, t.nights))
