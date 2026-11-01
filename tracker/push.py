"""Пуш-уведомления в установленное приложение (Web Push, стандарт VAPID).

Подписки телефонов лежат в папке push/ репозитория: приложение открывает GitHub
с готовым файлом, владелец нажимает «Commit changes». Отправить пуш по подписке
может только тот, у кого есть закрытый ключ VAPID. Ключ не хранится нигде:
он каждый раз выводится из секрета (PUSH_SECRET или TRAVELPAYOUTS_TOKEN).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
TTL = 12 * 3600  # если телефон без сети, пуш подождёт до 12 часов


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def device_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()[:12]


def derive_key(seed: str):
    from cryptography.hazmat.primitives.asymmetric import ec

    digest = hmac.new(seed.encode(), b"ski-tickets/vapid/p256", hashlib.sha256).digest()
    scalar = int.from_bytes(digest, "big") % (P256_ORDER - 1) + 1
    return ec.derive_private_key(scalar, ec.SECP256R1())


def public_key_b64(key) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return b64url(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))


@dataclass
class Device:
    id: str
    path: Path
    info: dict


class Push:
    def __init__(self, seed: str | None, root: Path, contact: str | None, *, dry_run: bool = False) -> None:
        self.directory = Path(root) / "push"
        self.contact = contact if contact and contact.startswith(("https://", "mailto:")) else "https://github.com"
        self.dry_run = dry_run
        self.outbox: list[dict] = []
        self._key = derive_key(seed) if seed else None
        self.public_key = public_key_b64(self._key) if self._key else None

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def devices(self) -> list[Device]:
        if not self.enabled or not self.directory.is_dir():
            return []
        found: dict[str, Device] = {}
        for path in sorted(self.directory.glob("*.json")):
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
                endpoint = info["endpoint"]
                keys = info["keys"]
                if not (isinstance(endpoint, str) and endpoint.startswith("https://") and keys.get("p256dh") and keys.get("auth")):
                    raise ValueError("нет endpoint или ключей")
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                log.warning("Подписка %s не читается: %s", path.name, exc)
                continue
            device = Device(device_id(endpoint), path, {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}})
            found.setdefault(device.id, device)
        return list(found.values())

    def send(self, device: Device, payload: dict) -> str:
        """Возвращает "ok", "gone" (подписка больше не существует) или "error"."""
        if self.dry_run:
            self.outbox.append({"device": device.id, **payload})
            return "ok"
        from py_vapid import Vapid
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info=device.info,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=Vapid(private_key=self._key),
                vapid_claims={"sub": self.contact},   # новый словарь на каждый запрос: pywebpush дописывает в него aud
                ttl=TTL,
                headers={"Urgency": "normal"},
                timeout=20,
            )
            return "ok"
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                return "gone"
            log.warning("Пуш на %s не ушёл: HTTP %s", device.id, status)
            return "error"
        except Exception as exc:  # сеть, неверные ключи подписки и т. п.
            log.warning("Пуш на %s не ушёл: %s", device.id, type(exc).__name__)
            return "error"
