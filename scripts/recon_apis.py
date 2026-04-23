"""Hit the 3 backend APIs directly and dump a sample of what they return,
so we can design the scraper modules on a real payload.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


SYMBOL = "CPALL"
OUT = Path("scripts/recon_out")
OUT.mkdir(parents=True, exist_ok=True)

FROM_DATE = "23/04/2020"  # 6 years back
TO_DATE = "23/04/2026"

APIS = {
    "stock_news": (
        f"https://www.set.or.th/api/set/news/search?symbol={SYMBOL}"
        f"&fromDate={FROM_DATE}&toDate={TO_DATE}&keyword=&lang=th"
    ),
    "corporate_action": (
        f"https://www.set.or.th/api/set/stock/{SYMBOL}/corporate-action?lang=th"
    ),
    "news_center": (
        "https://www.set.or.th/api/cms/v1/news/set"
        f"?sourceId=company&securityTypeIds=S"
        f"&fromDate={FROM_DATE}&toDate={TO_DATE}"
        f"&perPage=500&orderBy=date&lang=th"
    ),
}


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            locale="th-TH",
        )

        # Warm up: visit one page so we get the Incapsula cookie + session
        bootstrap = context.new_page()
        bootstrap.goto(
            f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/news",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        bootstrap.wait_for_timeout(5000)
        bootstrap.close()

        # Now hit the APIs directly using the same context (which carries cookies)
        for name, url in APIS.items():
            print(f"\n{'='*60}\n{name}\n{'='*60}")
            print(f"URL: {url[:120]}")
            try:
                resp = context.request.get(url, headers={
                    "Accept": "application/json",
                    "Referer": f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/news",
                })
                print(f"Status: {resp.status}")
                if resp.status == 200:
                    data = resp.json()
                    out_path = OUT / f"{name}_api.json"
                    out_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"Saved: {out_path}")
                    # Preview structure
                    if isinstance(data, dict):
                        keys = list(data.keys())
                        print(f"Keys: {keys}")
                        for k in keys:
                            v = data[k]
                            if isinstance(v, list):
                                print(f"  {k}: list[{len(v)}]")
                                if v and isinstance(v[0], dict):
                                    print(f"    item keys: {list(v[0].keys())}")
                            else:
                                preview = str(v)[:80]
                                print(f"  {k}: {preview}")
                    elif isinstance(data, list):
                        print(f"list[{len(data)}]")
                        if data and isinstance(data[0], dict):
                            print(f"  item keys: {list(data[0].keys())}")
                else:
                    print(f"Body: {resp.text()[:500]}")
            except Exception as e:
                print(f"ERROR: {e}")

        browser.close()


if __name__ == "__main__":
    run()
