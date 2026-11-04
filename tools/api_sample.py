"""Образцы ответов официального API Travelpayouts (для разработки): какие поля приходят."""
import json, os, sys
from pathlib import Path
import requests

TOKEN = os.environ["TRAVELPAYOUTS_TOKEN"]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "probe/api")
OUT.mkdir(parents=True, exist_ok=True)
H = {"X-Access-Token": TOKEN}
CALLS = {
    "v3_prices_for_dates_ow": ("https://api.travelpayouts.com/aviasales/v3/prices_for_dates",
        dict(origin="MOW", destination="AER", departure_at="2027-03", one_way="true", direct="true", limit=5, currency="rub")),
    "v3_prices_for_dates_rt": ("https://api.travelpayouts.com/aviasales/v3/prices_for_dates",
        dict(origin="MOW", destination="AER", departure_at="2027-03", return_at="2027-03", one_way="false", direct="true", limit=5, currency="rub")),
    "v3_get_latest_prices": ("https://api.travelpayouts.com/aviasales/v3/get_latest_prices",
        dict(origin="MOW", destination="AER", beginning_of_period="2027-03-01", period_type="month", one_way="true", limit=5, currency="rub", sorting="price")),
    "v2_latest": ("https://api.travelpayouts.com/v2/prices/latest",
        dict(origin="MOW", destination="AER", beginning_of_period="2027-03-01", period_type="month", one_way="true", limit=5, currency="rub", sorting="price", show_to_affiliates="false")),
    "v2_month_matrix": ("https://api.travelpayouts.com/v2/prices/month-matrix",
        dict(origin="MOW", destination="AER", month="2027-03-01", show_to_affiliates="false", currency="rub")),
}
for name, (url, params) in CALLS.items():
    try:
        r = requests.get(url, params=params, headers=H, timeout=40)
        body = r.json() if "json" in r.headers.get("content-type", "") else r.text[:2000]
    except Exception as exc:
        r, body = None, f"{type(exc).__name__}: {exc}"
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        body["data"] = body["data"][:5]
    (OUT / f"{name}.json").write_text(json.dumps({"status": r.status_code if r is not None else None, "body": body}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name, r.status_code if r is not None else "-")
