"""Find the max date range the /api/set/news/search endpoint allows."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright


SYMBOL = "CPALL"
OUT = Path("scripts/recon_out")

# Try several ranges to narrow down the limit
RANGES = [
    ("1y", 365),
    ("2y", 730),
    ("3y", 1095),
    ("4y", 1460),
    ("5y", 1825),
    ("6y", 2190),
]


def fmt(d):
    return d.strftime("%d/%m/%Y")


def run():
    today = datetime(2026, 4, 23)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="th-TH")

        # Warm up
        page = context.new_page()
        page.goto(
            f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/news",
            wait_until="domcontentloaded", timeout=60_000
        )
        page.wait_for_timeout(4000)
        page.close()

        results = []
        for label, days in RANGES:
            from_d = today - timedelta(days=days)
            url = (
                f"https://www.set.or.th/api/set/news/search"
                f"?symbol={SYMBOL}&fromDate={fmt(from_d)}&toDate={fmt(today)}"
                "&keyword=&lang=th"
            )
            try:
                resp = context.request.get(url, headers={
                    "Referer": f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/news"
                })
                body = resp.text()
                status = resp.status
                count = None
                if status == 200:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        items = data.get("newsInfoList") or data.get("items") or data.get("data") or []
                        count = len(items) if isinstance(items, list) else "?"
                    elif isinstance(data, list):
                        count = len(data)
                print(f"  {label:4}  {fmt(from_d)}..{fmt(today)}  "
                      f"[{status}]  items={count}  {body[:80] if status != 200 else ''}")
                results.append({"range": label, "status": status, "count": count})
            except Exception as e:
                print(f"  {label}  ERROR: {e}")

        # Also: fetch one working range and save the response
        working = next((r for r in results if r.get("count") and r["count"] not in (None, "?")),
                      None)
        if working:
            print(f"\nFetching full '{working['range']}' payload for schema design...")
            days = dict(RANGES)[working["range"]]
            from_d = today - timedelta(days=days)
            url = (
                f"https://www.set.or.th/api/set/news/search"
                f"?symbol={SYMBOL}&fromDate={fmt(from_d)}&toDate={fmt(today)}"
                "&keyword=&lang=th"
            )
            resp = context.request.get(url, headers={
                "Referer": f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/news"
            })
            data = resp.json()
            (OUT / "stock_news_api.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"Saved: {OUT / 'stock_news_api.json'}")

        browser.close()


if __name__ == "__main__":
    run()
