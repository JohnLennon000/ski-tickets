"""Пробная проверка: открываются ли сайты авиакомпаний через телефон (Tailscale).

Открывает страницы в настоящем Chromium, сохраняет скриншоты и короткий отчёт в probe/.
Цены не собирает — только проверяет, пускают ли сайты.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = [
    ("aeroflot", "https://www.aeroflot.ru/ru-ru"),
    ("aeroflot-search", "https://www.aeroflot.ru/sb/app/ru-ru#/search?adults=1&cabin=economy&children=0&infants=0&routes=MOW.20270303.AER-AER.20270311.MOW"),
    ("pobeda", "https://www.flypobeda.ru/"),
    ("s7", "https://www.s7.ru/"),
    ("utair", "https://www.utair.ru/"),
    ("uralairlines", "https://www.uralairlines.ru/"),
]


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    report = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow", viewport={"width": 1280, "height": 900})
        for name, url in PAGES:
            page = ctx.new_page()
            item = {"name": name, "url": url}
            started = time.time()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(8000)   # даём отработать проверкам «не робот ли вы» и подгрузке
                item.update(
                    status=resp.status if resp else None,
                    final_url=page.url,
                    title=page.title(),
                    text=" ".join(page.inner_text("body").split())[:400],
                )
                page.screenshot(path=str(out / f"{name}.png"))
            except Exception as exc:  # отчёт важнее одной страницы
                item["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            item["seconds"] = round(time.time() - started, 1)
            report.append(item)
            print(json.dumps(item, ensure_ascii=False))
            page.close()
            time.sleep(5)
        browser.close()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "probe")))
