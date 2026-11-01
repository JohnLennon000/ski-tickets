"""Клиент Aviasales Data API (Travelpayouts).

Это кэш цен: билеты, которые другие люди находили в поиске Aviasales
за последние дни. Живой поиск через API доступен только крупным партнёрам.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Protocol

import requests

log = logging.getLogger(__name__)

URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
PAGE_LIMIT = 1000
MAX_PAGES = 3


class ApiError(RuntimeError):
    pass


class AuthError(ApiError):
    pass


class PricesSource(Protocol):
    def fetch(self, origin: str, destination: str, departure_at: str, return_at: str | None = None) -> list[dict]:
        ...


def fixture_name(origin: str, destination: str, departure_at: str, return_at: str | None) -> str:
    return f"{origin}-{destination}-{departure_at}-{return_at or 'ow'}.json"


class PricesApi:
    def __init__(
        self,
        token: str,
        *,
        direct: bool = True,
        pause: float = 0.25,
        session: requests.Session | None = None,
        dump_dir: Path | None = None,
    ) -> None:
        self.token = token
        self.direct = direct
        self.pause = pause
        self.session = session or requests.Session()
        self.dump_dir = dump_dir

    def fetch(self, origin: str, destination: str, departure_at: str, return_at: str | None = None) -> list[dict]:
        params: dict[str, str | int] = {
            "origin": origin,
            "destination": destination,
            "departure_at": departure_at,
            "one_way": "false" if return_at else "true",
            "sorting": "price",
            "limit": PAGE_LIMIT,
            "currency": "rub",
            "market": "ru",
        }
        if return_at:
            params["return_at"] = return_at
        if self.direct:
            params["direct"] = "true"
        items: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            params["page"] = page
            chunk = self._get(params)
            items.extend(chunk)
            if len(chunk) < PAGE_LIMIT:
                break
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            path = self.dump_dir / fixture_name(origin, destination, departure_at, return_at)
            path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        return items

    def _get(self, params: dict) -> list[dict]:
        delay = 2.0
        problem = "нет ответа"
        for _attempt in range(5):
            if self.pause:
                time.sleep(self.pause)
            try:
                resp = self.session.get(
                    URL,
                    params=params,
                    headers={"X-Access-Token": self.token, "Accept-Encoding": "gzip"},
                    timeout=40,
                )
            except requests.RequestException as exc:
                problem = type(exc).__name__
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 401:
                raise AuthError("Travelpayouts не принял токен (HTTP 401). Проверьте секрет TRAVELPAYOUTS_TOKEN.")
            if resp.status_code == 429 or resp.status_code >= 500:
                problem = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After", "")
                wait = min(float(retry_after), 60.0) if retry_after.isdigit() else delay
                time.sleep(wait)
                delay *= 2
                continue
            if resp.status_code >= 400:
                raise ApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise ApiError(f"ответ не JSON: {resp.text[:200]}") from exc
            if isinstance(payload, dict) and payload.get("success") is False:
                raise ApiError(str(payload.get("error") or payload)[:300])
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, list) else []
        raise ApiError(f"API не отвечает: {problem}")


class FixtureApi:
    """Читает сохранённые ответы API — для тестов и проверки без интернета."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def fetch(self, origin: str, destination: str, departure_at: str, return_at: str | None = None) -> list[dict]:
        path = self.directory / fixture_name(origin, destination, departure_at, return_at)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))
