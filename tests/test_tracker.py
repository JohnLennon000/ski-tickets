import dataclasses
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from fake_api import write_fixtures
from tracker.alerts import evaluate
from tracker.config import ConfigError, Resort, load_config
from tracker.main import main, seal, unseal
from tracker.market import Fare, Flight, Market, parse_one_way, parse_round_trip
from tracker.messages import split_message
from tracker.store import BestRow, Store
from tracker.trips import Trip, best_trip, build_trips

ROOT = Path(__file__).resolve().parent.parent
D = dt.date
UTC = dt.timezone.utc


@pytest.fixture(scope="module")
def cfg():
    return load_config(ROOT / "config.yaml")


def ow(origin, dest, day, price, airline="SU", bag=2000, hour=10):
    flight = Flight(origin, dest, airline, f"{airline} 100", f"{day.isoformat()}T{hour:02d}:00:00+03:00")
    return Fare("ow", (flight,), price, bag, f"https://www.aviasales.ru/search/x{price}")


def rt(dest, first, last, price, airline="SU", bag=4000):
    out = Flight("SVO", dest, airline, f"{airline} 1", f"{first.isoformat()}T09:00:00+03:00")
    back = Flight(dest, "SVO", airline, "", f"{last.isoformat()}T18:00:00+03:00")
    return Fare("rt", (out, back), price, bag, "https://www.aviasales.ru/search/rt")


# --- настройки -------------------------------------------------------------------

def test_config(cfg):
    assert cfg.origin == "MOW"
    assert (cfg.start, cfg.end) == (D(2027, 1, 30), D(2027, 3, 14))
    caucasus = next(r for r in cfg.resorts if r.id == "caucasus")
    assert caucasus.nights == (8,)
    assert caucasus.airports == ("MRV", "NAL")
    assert next(r for r in cfg.resorts if r.id == "polyana").nights == (8,)
    assert next(r for r in cfg.resorts if r.id == "sheregesh").nights == (7, 8)
    assert cfg.baggage_fee("DP") == 2500
    assert cfg.baggage_fee("ZZ") == 2000
    assert cfg.low_priority[0].start == D(2027, 2, 8) and cfg.low_priority[0].end == D(2027, 2, 21)


def test_config_friendly_values(tmp_path):
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    assert "\n    threshold:\n" in text
    text = text.replace("\n    threshold:\n", "\n    threshold: 16 000 ₽\n", 1)    # первый курорт — Красная Поляна
    text = text.replace("from: 2027-02-08", "from: 10.02.2027")
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.resorts[0].threshold == 16000
    assert cfg.low_priority[0].start == D(2027, 2, 10)

    (tmp_path / "config.yaml").write_text(text.replace("threshold: 16 000 ₽", "threshold: дёшево"), encoding="utf-8")
    with pytest.raises(ConfigError, match="Красная Поляна"):
        load_config(tmp_path / "config.yaml")


# --- даты поездок ------------------------------------------------------------------

def test_trip_dates(cfg):
    trips = build_trips(cfg, Market())
    sheregesh = trips["sheregesh"]
    eligible = [t for t in sheregesh if t.eligible]
    early = sorted(t.key for t in eligible if t.depart < D(2027, 2, 8))
    assert early == ["2027-01-30/7", "2027-01-30/8", "2027-01-31/7"]
    assert not next(t for t in sheregesh if t.key == "2027-01-31/8").eligible   # возвращение 08.02
    assert not next(t for t in sheregesh if t.key == "2027-02-21/7").eligible   # вылет 21.02
    late = [t for t in eligible if t.depart >= D(2027, 2, 22)]
    assert min(t.depart for t in late) == D(2027, 2, 22)
    assert max(t.ret for t in late) == D(2027, 3, 14)
    assert len(late) == 14 + 13
    for rid in ("caucasus", "polyana"):                       # Минводы и Сочи — только 8 ночей
        only8 = [t for t in trips[rid] if t.eligible]
        assert {t.nights for t in only8} == {8}
        assert [t.key for t in only8 if t.depart < D(2027, 2, 8)] == ["2027-01-30/8"]
        assert len(only8) == 1 + 13


def test_open_jaw_and_round_trip(cfg):
    market = Market()
    market.out["MRV"] = {D(2027, 1, 30): ow("SVO", "MRV", D(2027, 1, 30), 6000, "DP", bag=2500)}
    market.back["MRV"] = {D(2027, 2, 7): ow("MRV", "SVO", D(2027, 2, 7), 6000)}
    market.back["NAL"] = {D(2027, 2, 7): ow("NAL", "VKO", D(2027, 2, 7), 5000, "UT")}
    market.out["AER"] = {D(2027, 2, 22): ow("VKO", "AER", D(2027, 2, 22), 8000)}
    market.back["AER"] = {D(2027, 3, 2): ow("AER", "VKO", D(2027, 3, 2), 8000)}
    market.rt["AER"] = {(D(2027, 2, 22), D(2027, 3, 2)): rt("AER", D(2027, 2, 22), D(2027, 3, 2), 11000)}
    trips = build_trips(cfg, market)

    jaw = next(t for t in trips["caucasus"] if t.key == "2027-01-30/8")
    assert jaw.total == 6000 + 2500 + 5000 + 2000
    assert jaw.airports == "MRV/NAL"
    assert jaw.airlines == "DP/UT"
    assert jaw.search_link("MOW").startswith("https://www.aviasales.ru/search/x")   # разные аэропорты — ссылка на «туда»

    sochi = next(t for t in trips["polyana"] if t.key == "2027-02-22/8")
    assert sochi.rt is not None and sochi.total == 15000          # одной бронью дешевле, чем 20 000
    assert sochi.search_link("MOW") == "https://www.aviasales.ru/search/MOW2202AER02031"
    assert best_trip(trips["polyana"]) is sochi


def test_best_trip_ignores_conference_dates(cfg):
    market = Market()
    market.out["AER"] = {D(2027, 2, 10): ow("VKO", "AER", D(2027, 2, 10), 3000),
                         D(2027, 2, 24): ow("VKO", "AER", D(2027, 2, 24), 9000)}
    market.back["AER"] = {D(2027, 2, 18): ow("AER", "VKO", D(2027, 2, 18), 3000),
                          D(2027, 3, 4): ow("AER", "VKO", D(2027, 3, 4), 9000)}
    trips = build_trips(cfg, market)["polyana"]
    assert next(t for t in trips if t.key == "2027-02-10/8").total == 10000   # есть цена, но это конференция
    assert best_trip(trips).key == "2027-02-24/8"


def test_parse_filters_transfers(cfg):
    item = {"origin_airport": "VKO", "destination_airport": "AER", "price": 5100, "airline": "DP",
            "flight_number": 123, "departure_at": "2027-02-22T07:15:00+03:00", "transfers": 0,
            "link": "/search/MOW2202AER1?t=DP1"}
    fare = parse_one_way(item, cfg)
    assert fare.total == 5100 + 2500
    assert fare.flights[0].number == "DP 123" and fare.flights[0].time == "07:15"
    assert fare.link == "https://www.aviasales.ru/search/MOW2202AER1?t=DP1"
    assert parse_one_way({**item, "transfers": 1}, cfg) is None
    assert parse_one_way({**item, "price": None}, cfg) is None
    assert parse_round_trip({**item, "return_at": "2027-03-01T18:00:00+03:00", "return_transfers": 1}, cfg) is None
    fare = parse_round_trip({**item, "return_at": "2027-03-01T18:00:00+03:00", "return_transfers": 0}, cfg)
    assert fare.bag == 5000 and fare.flights[1].date == D(2027, 3, 1)


# --- уведомления ---------------------------------------------------------------

def priced_trip(total, day=D(2027, 2, 22), nights=7):
    trip = Trip("polyana", day, day + dt.timedelta(days=nights), nights)
    trip.rt = rt("AER", trip.depart, trip.ret, total, bag=0)
    return trip


def test_alert_flow(cfg):
    cfg = dataclasses.replace(cfg, resorts=(Resort("polyana", "Красная Поляна", ("AER",), (7, 8), 15000),))
    state: dict = {}
    history: list[BestRow] = []
    t0 = dt.datetime(2026, 11, 1, 5, 17, tzinfo=UTC)

    def check(hours, total):
        now = t0 + dt.timedelta(hours=hours)
        alerts = evaluate(cfg, state, {"polyana": priced_trip(total)}, history, now)
        history.append(BestRow(now, "polyana", total, "2027-02-22", 7))
        return sorted(e.kind for a in alerts for e in a.events)

    assert check(0, 20000) == []                       # первая проверка — только запоминаем
    assert check(3, 19900) == []                       # −0,5%: меньше шага в 1%
    assert state["resorts"]["polyana"]["min_ever"] == 19900
    assert check(6, 18900) == ["drop", "new_min"]      # −5% к минимуму суток и новый минимум
    assert check(9, 18900) == []                       # то же самое — молчим
    assert check(12, 14900) == ["drop", "new_min", "threshold"]
    assert check(15, 14800) == []                      # чуть ниже — не повод
    assert check(18, 16000) == []                      # выше порога — порог снова взведён
    assert check(21, 15000) == ["threshold"]
    assert check(48, 21000) == []


# --- хранение -------------------------------------------------------------------

def test_trip_history_only_changes(tmp_path):
    store = Store(tmp_path)
    state: dict = {}
    now = dt.datetime(2026, 11, 1, 5, 17, tzinfo=UTC)
    trips = {"polyana": [priced_trip(20000), Trip("polyana", D(2027, 3, 1), D(2027, 3, 8), 7)]}
    assert store.append_trip_changes(now, trips, state) == 1           # без цены — не пишем
    assert store.append_trip_changes(now, trips, state) == 0           # ничего не поменялось
    trips["polyana"][0] = priced_trip(19000)
    assert store.append_trip_changes(now, trips, state) == 1
    trips["polyana"][0] = Trip("polyana", D(2027, 2, 22), D(2027, 3, 1), 7)
    assert store.append_trip_changes(now, trips, state) == 1           # цена пропала — тоже факт
    lines = (tmp_path / "docs/data/trips/polyana.csv").read_text().splitlines()
    assert lines[0] == "time,depart,nights,total,price,bag,airports,airlines"
    assert [line.split(",")[3] for line in lines[1:]] == ["20000", "19000", ""]


def test_seal_roundtrip():
    blob = seal("123456789", "bot-token")
    assert "123456789" not in blob
    assert unseal(blob, "bot-token") == "123456789"
    assert unseal(blob, "other-token") is None


def test_split_message():
    text = "\n\n".join(f"<b>блок {i}</b>\n" + "x" * 900 for i in range(10))
    parts = split_message(text)
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
    assert all(p.count("<b>") == p.count("</b>") for p in parts)


# --- целиком: две проверки подряд ------------------------------------------------

def test_end_to_end(tmp_path, capsys, monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_REPOSITORY", "SITE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "user/ski-tickets")
    root = tmp_path / "project"
    root.mkdir()
    shutil.copy(ROOT / "config.yaml", root / "config.yaml")
    cfg = load_config(root / "config.yaml")
    write_fixtures(cfg, tmp_path / "day1", seed=7)
    write_fixtures(cfg, tmp_path / "day1b", seed=7, shift=0.8)

    def run(fixtures, now):
        assert main(["--root", str(root), "--fixtures", str(tmp_path / fixtures), "--dry-run", "--now", now]) == 0
        return capsys.readouterr().out

    out = run("day1", "2026-11-02T05:17Z")                     # 08:17 по Москве
    assert "Слежу за ценами" in out
    assert "https://user.github.io/ski-tickets/" in out
    assert "----- photo" in out

    summary = json.loads((root / "docs/data/summary.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in summary["resorts"]] == [r.id for r in cfg.resorts]
    for resort in summary["resorts"]:
        priced = [t for t in resort["trips"] if t["total"] and not t["low"]]
        if priced:
            best = next(t for t in resort["trips"] if t["key"] == resort["best"])
            assert best["total"] == min(t["total"] for t in priced)
            assert best["legs"][0]["dir"] == "out" and best["link"].startswith("https://www.aviasales.ru/")

    out = run("day1", "2026-11-02T08:17Z")                     # те же цены — тишина
    assert "-----" not in out
    assert (root / "docs/data/best.csv").read_text().count("\n") == 1 + 2 * len(cfg.resorts)

    out = run("day1b", "2026-11-02T11:17Z")                    # всё подешевело на 20%
    assert "новый минимум" in out.lower()
    assert "Проверить на Aviasales" in out

    out = run("day1b", "2026-11-03T05:17Z")                    # следующее утро — сводка
    assert "Цены на 3 ноября" in out
    out = run("day1b", "2026-11-03T08:17Z")                    # второй раз за день не шлём
    assert "Цены на" not in out


# --- пуш-уведомления в приложение ----------------------------------------------------

def test_push_keys_are_stable(tmp_path):
    from tracker.push import Push

    a = Push("секрет", tmp_path, None)
    b = Push("секрет", tmp_path, None)
    c = Push("другой", tmp_path, None)
    assert a.public_key == b.public_key != c.public_key
    import base64
    raw = base64.urlsafe_b64decode(a.public_key + "==")
    assert len(raw) == 65 and raw[0] == 4          # несжатая точка P-256 — формат applicationServerKey
    assert not Push(None, tmp_path, None).enabled


def write_device(root, name, endpoint):
    (root / "push").mkdir(exist_ok=True)
    (root / "push" / name).write_text(json.dumps({
        "endpoint": endpoint, "expirationTime": None,
        "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                 "auth": "tBHItJI5svbpez7KI4CCXg"},
    }), encoding="utf-8")


def test_push_flow(tmp_path, capsys, monkeypatch):
    from tracker.push import device_id

    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TRAVELPAYOUTS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PUSH_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_REPOSITORY", "user/ski-tickets")
    root = tmp_path / "project"
    root.mkdir()
    shutil.copy(ROOT / "config.yaml", root / "config.yaml")
    cfg = load_config(root / "config.yaml")
    write_fixtures(cfg, tmp_path / "a", seed=5)
    write_fixtures(cfg, tmp_path / "b", seed=5, shift=0.8)
    endpoint = "https://fcm.googleapis.com/fcm/send/abc123"
    write_device(root, "phone.json", endpoint)
    write_device(root, "broken.json", "http://not-https")

    def run(fixtures, now):
        assert main(["--root", str(root), "--fixtures", str(tmp_path / fixtures), "--dry-run", "--now", now]) == 0
        return capsys.readouterr().out

    out = run("a", "2026-11-02T05:17Z")
    assert out.count("----- push") == 1 and "Уведомления включены" in out     # Telegram не настроен — только пуш
    summary = json.loads((root / "docs/data/summary.json").read_text(encoding="utf-8"))
    assert summary["push"]["devices"] == [device_id(endpoint)]
    assert len(summary["push"]["vapid"]) == 87

    out = run("b", "2026-11-02T11:17Z")                                         # подешевело — пуш по курортам
    assert out.count("----- push") >= 3 and "Новый минимум" in out and "[./#polyana/" in out

    out = run("b", "2026-11-03T05:17Z")                                         # утро — одна сводка
    assert out.count("----- push") == 1 and "Цены на 3 ноября" in out

    import tracker.main as tm
    monkeypatch.setattr(tm.Push, "send", lambda self, device, payload: "gone")  # пуш-сервис: подписки больше нет
    run("b", "2026-11-04T05:17Z")
    assert not (root / "push" / "phone.json").exists()                          # подписка удалена с телефона — файл убран
    summary = json.loads((root / "docs/data/summary.json").read_text(encoding="utf-8"))
    assert summary["push"]["devices"] == []
