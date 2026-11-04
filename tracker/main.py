"""Одна проверка цен: собрать, сохранить, уведомить, обновить данные сайта."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import logging
import os
from pathlib import Path

from tracker.alerts import ResortAlert, evaluate
from tracker.api import AuthError, FixtureApi, PricesApi
from tracker.chart import digest_chart
from tracker.checks import load_checks
from tracker.config import Config, ConfigError, load_config
from tracker.market import collect
from tracker.messages import (
    alert_message,
    digest_message,
    problem_message,
    push_alerts,
    push_digest,
    push_problem,
)
from tracker.push import Push
from tracker.store import BestRow, Store
from tracker.summary import build_summary
from tracker.telegram import Telegram
from tracker.trips import Trip, best_trip, build_trips
from tracker.util import MSK, human_day, iso_utc, parse_utc

log = logging.getLogger("tracker")

PROBLEM_REPEAT = dt.timedelta(hours=12)
# Меняется, когда меняется способ расчёта цены (багаж, отбор рейсов): старые минимумы и пороги
# посчитаны иначе, их нужно забыть, иначе будут ложные «минимум был …».
METHOD = 3


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tracker", description="Проверка цен на авиабилеты")
    parser.add_argument("--root", default=".", help="папка проекта (где config.yaml)")
    parser.add_argument("--fixtures", help="брать ответы API из папки, а не из интернета")
    parser.add_argument("--dump", help="сохранить ответы API в папку")
    parser.add_argument("--dry-run", action="store_true", help="не отправлять уведомления, а напечатать")
    parser.add_argument("--now", help="время проверки в ISO, для тестов")
    parser.add_argument("--force-digest", action="store_true", help="отправить сводку сейчас")
    return parser.parse_args(argv)


def site_url_from_env() -> str | None:
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def edit_url_from_env() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    return f"https://github.com/{repo}/edit/{os.environ.get('GITHUB_REF_NAME') or 'main'}/config.yaml"


# chat_id хранится в публичном репозитории только в зашифрованном виде:
# ключ получается из токена бота, а токен лежит в секретах GitHub.
def seal(chat_id: str, token: str) -> str:
    data = chat_id.encode()
    key = hashlib.sha256(("chat:" + token).encode()).digest()
    return base64.urlsafe_b64encode(bytes(b ^ key[i % len(key)] for i, b in enumerate(data))).decode()


def unseal(blob: str, token: str) -> str | None:
    try:
        data = base64.urlsafe_b64decode(blob.encode())
    except (ValueError, TypeError):
        return None
    key = hashlib.sha256(("chat:" + token).encode()).digest()
    text = bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode(errors="replace")
    return text if text.lstrip("-").isdigit() else None


class Notifier:
    """Telegram: необязательный канал, работает, если задан TELEGRAM_BOT_TOKEN."""

    def __init__(self, tg: Telegram, state: dict) -> None:
        self.tg = tg
        self.state = state.setdefault("telegram", {})
        self._ready: bool | None = None

    def ready(self) -> bool:
        if self._ready is None:
            self._ready = self._connect()
        return self._ready

    def _connect(self) -> bool:
        if not self.tg.configured:
            log.info("Telegram не настроен (нет TELEGRAM_BOT_TOKEN)")
            return False
        if self.tg.chat_id:
            return True
        sealed = self.state.get("chat")
        if sealed and not self.tg.dry_run:
            self.tg.chat_id = unseal(sealed, self.tg.token)
            if self.tg.chat_id:
                return True
        chat = self.tg.discover_chat()
        if not chat:
            log.warning("Бот ещё не знает, кому писать: откройте его в Telegram и нажмите «Запустить» (/start)")
            return False
        self.tg.chat_id = chat
        if not self.tg.dry_run:
            self.state["chat"] = seal(chat, self.tg.token)
        return True


def report_problem(notifier: Notifier, push: Push, state: dict, now: dt.datetime, text: str) -> None:
    last = state.get("problem_at")
    if last and now - parse_utc(last) < PROBLEM_REPEAT:
        return
    sent = False
    if notifier.ready():
        sent = notifier.tg.send_message(problem_message(text)) or sent
    known = set(state.get("push", {}).get("known", []))
    for device in push.devices():
        if device.id in known:
            sent = push.send(device, push_problem(text)) == "ok" or sent
    if sent:
        state["problem_at"] = iso_utc(now)


def deliver(
    notifier: Notifier,
    push: Push,
    cfg: Config,
    state: dict,
    bests: dict[str, Trip | None],
    history: list[BestRow],
    alerts: list[ResortAlert],
    now: dt.datetime,
    *,
    force_digest: bool = False,
) -> None:
    local = now.astimezone(MSK)
    silent = cfg.alerts.is_quiet(local.hour)
    today = local.date().isoformat()
    morning = local.hour >= cfg.alerts.digest_hour
    digest_due = force_digest or (morning and state.get("digest_day") != today)
    digest_sent = False

    # --- Telegram -------------------------------------------------------------------
    if notifier.ready():
        tg, tstate = notifier.tg, notifier.state
        site_url, edit_url = site_url_from_env(), edit_url_from_env()

        def send_digest(welcome: bool, merged: list[ResortAlert] | None = None) -> bool:
            text = digest_message(cfg, bests, state, history, now, site_url, edit_url, welcome=welcome, alerts=merged)
            if not tg.send_message(text, silent=silent):
                return False
            try:
                png = digest_chart(cfg, history, now)
            except Exception:  # картинка — дополнение, сводка уже ушла
                log.exception("Не удалось нарисовать график")
                return True
            tg.send_photo(png, f"Динамика лучшей цены · {human_day(local.date())}", silent=True)
            return True

        if not tstate.get("welcomed"):
            if send_digest(welcome=True):
                tstate["welcomed"] = True
                digest_sent = digest_sent or morning
        elif digest_due:
            # Утром поводы для уведомлений входят в сводку — одно сообщение вместо двух.
            digest_sent = send_digest(welcome=False, merged=alerts) or digest_sent
        elif alerts:
            tg.send_message(alert_message(alerts, cfg, site_url), silent=silent)

    # --- пуш в приложение --------------------------------------------------------------
    if push.enabled:
        pstate = state.setdefault("push", {})
        known = set(pstate.get("known", []))
        devices = push.devices()
        for device in devices:
            if device.id not in known:
                result = push.send(device, push_digest(cfg, bests, history, now, welcome=True))
                if result == "ok":
                    known.add(device.id)
                    log.info("Подключён телефон %s", device.id)
            elif digest_due:
                result = push.send(device, push_digest(cfg, bests, history, now, alerts=alerts, silent=silent))
                digest_sent = digest_sent or result == "ok"
            else:
                result = "ok"
                for payload in push_alerts(alerts, silent=silent):
                    result = push.send(device, payload)
                    if result == "gone":
                        break
            if result == "gone":
                log.info("Подписка %s больше не действует — удаляю %s", device.id, device.path.name)
                device.path.unlink(missing_ok=True)
                known.discard(device.id)
        pstate["known"] = sorted(known & {d.id for d in devices if d.path.exists()})

    if digest_sent:
        state["digest_day"] = today


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = Path(args.root).resolve()
    store = Store(root)
    state = store.load_state()
    now = (parse_utc(args.now) if args.now else dt.datetime.now(dt.timezone.utc)).replace(second=0, microsecond=0)
    tg = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID") or None,
                  dry_run=args.dry_run)
    notifier = Notifier(tg, state)
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
    push = Push(os.environ.get("PUSH_SECRET") or token or None, root, site_url_from_env(), dry_run=args.dry_run)

    def fail(message: str) -> int:
        log.error("%s", message)
        report_problem(notifier, push, state, now, message)
        store.save_state(state)
        return 1

    try:
        cfg = load_config(root / "config.yaml")
    except ConfigError as exc:
        return fail(f"Ошибка в config.yaml: {exc}")

    if args.fixtures:
        api = FixtureApi(Path(args.fixtures))
    elif not token:
        return fail("Не задан секрет TRAVELPAYOUTS_TOKEN (Settings → Secrets and variables → Actions)")
    else:
        api = PricesApi(token, direct=cfg.direct_only, dump_dir=Path(args.dump) if args.dump else None)

    try:
        market = collect(cfg, api, now.astimezone(MSK).date())
    except AuthError as exc:
        return fail(str(exc))

    log.info(
        "Запросов: %d успешно, %d с ошибкой; цен в кэше: %d; устаревших отброшено: %d",
        market.requests_ok, market.requests_failed, market.fares_count(), market.stale,
    )
    if market.requests_ok == 0:
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] >= 2:
            report_problem(notifier, push, state, now,
                           "Aviasales API не отвечает несколько проверок подряд.\n" + "\n".join(market.errors[:3]))
        store.save_state(state)
        return 0
    state["failures"] = 0

    if state.get("method") != METHOD:
        state.pop("resorts", None)
        state["method"] = METHOD
        log.info("Способ расчёта цены изменился — запомненные минимумы сброшены")

    trips = build_trips(cfg, market)
    bests = {r.id: best_trip(trips[r.id]) for r in cfg.resorts}
    alerts = evaluate(cfg, state, bests, store.read_best(), now)
    store.append_best(now, bests)
    changed = store.append_trip_changes(now, trips, state)
    history = store.read_best()

    for resort in cfg.resorts:
        best = bests[resort.id]
        priced = sum(1 for t in trips[resort.id] if t.priced)
        log.info(
            "%s: лучшая %s, поездок с ценой %d из %d",
            resort.name, f"{best.total} ({best.key})" if best else "—", priced, len(trips[resort.id]),
        )
    log.info("Изменений цен по поездкам: %d; поводов для уведомления: %d", changed, len(alerts))

    try:
        deliver(notifier, push, cfg, state, bests, history, alerts, now, force_digest=args.force_digest)
    except Exception:  # уведомления не должны мешать сохранить данные
        log.exception("Ошибка при отправке уведомлений")

    push_info = {"vapid": push.public_key, "devices": state.get("push", {}).get("known", [])} if push.enabled else None
    store.write_summary(
        build_summary(cfg, trips, bests, state, history, now, market, os.environ.get("GITHUB_REPOSITORY"), push_info,
                      load_checks(root, now))
    )
    store.save_state(state)

    if args.dry_run:
        for item in tg.outbox:
            print(f"\n----- {item['type']} (silent={item['silent']}) -----")
            print(item.get("text") or f"{item.get('caption')} [{item.get('size')} байт]")
        for item in push.outbox:
            print(f"\n----- push → {item['device']} (silent={item.get('silent', False)}) -----")
            print(f"{item['title']}\n{item['body']}\n[{item['url']}]")
    return 0
