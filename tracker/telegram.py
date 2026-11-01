"""Отправка сообщений в Telegram через Bot API."""
from __future__ import annotations

import json
import logging

import requests

from tracker.messages import split_message

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str | None, chat_id: str | None = None, *, dry_run: bool = False) -> None:
        self.token = token or ""
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.outbox: list[dict] = []   # в режиме dry_run сообщения складываются сюда

    @property
    def configured(self) -> bool:
        return self.dry_run or bool(self.token)

    def _call(self, method: str, data: dict, files: dict | None = None) -> object | None:
        url = API.format(token=self.token, method=method)
        try:
            resp = requests.post(url, data=data, files=files, timeout=40)
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Telegram %s: %s", method, str(exc).replace(self.token, "***"))
            return None
        if not payload.get("ok"):
            log.warning("Telegram %s: %s", method, payload.get("description"))
            return None
        return payload.get("result")

    def discover_chat(self) -> str | None:
        """Ищет личный чат, в котором боту написали (например, /start)."""
        if self.dry_run:
            return "dry-run"
        updates = self._call("getUpdates", {"timeout": 0, "allowed_updates": json.dumps(["message"])})
        for update in updates or []:
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private" and chat.get("id"):
                return str(chat["id"])
        return None

    def send_message(self, text: str, *, silent: bool = False) -> bool:
        if self.dry_run:
            self.outbox.append({"type": "message", "text": text, "silent": silent})
            return True
        ok = True
        for chunk in split_message(text):
            result = self._call(
                "sendMessage",
                {
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "link_preview_options": json.dumps({"is_disabled": True}),
                    "disable_notification": json.dumps(silent),
                },
            )
            ok = ok and result is not None
        return ok

    def send_photo(self, png: bytes, caption: str = "", *, silent: bool = False) -> bool:
        if self.dry_run:
            self.outbox.append({"type": "photo", "caption": caption, "silent": silent, "size": len(png)})
            return True
        result = self._call(
            "sendPhoto",
            {
                "chat_id": self.chat_id,
                "caption": caption[:1000],
                "parse_mode": "HTML",
                "disable_notification": json.dumps(silent),
            },
            files={"photo": ("prices.png", png, "image/png")},
        )
        return result is not None
