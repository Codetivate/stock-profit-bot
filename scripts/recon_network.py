"""Recon the backend APIs Next.js calls when rendering the 3 target pages.

If we find a JSON endpoint that returns the news list, we can call it
directly (with cookies obtained via Playwright) and skip HTML parsing.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


SYMBOL = "CPALL"
OUT = Path("scripts/recon_out")
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = {
    "news_center": (
        "https://www.set.or.th/th/market/news-and-alert/news"
        "?source=company&securityType=S&fromDate=2021-04-23&toDate=2026-04-23"
    ),
    "stock_news": f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/news",
    "rights_benefits": f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/rights-benefits",
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

        for name, url in TARGETS.items():
            print(f"\n{'='*60}\n{name}\n{'='*60}")
            page = context.new_page()
            recorded = []

            def on_response(resp, target=recorded):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct or "application/" in ct:
                        u = resp.url
                        # Skip static/analytics noise
                        skip = ("google", "gstatic", "googletag", "analytics",
                                "doubleclick", "facebook", ".svg", ".woff",
                                "_next/static", "Incapsula")
                        if any(s in u for s in skip):
                            return
                        target.append({
                            "url": u,
                            "status": resp.status,
                            "content_type": ct,
                            "method": resp.request.method,
                        })
                except Exception:
                    pass

            page.on("response", on_response)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(10_000)
            except Exception as e:
                print(f"  ERROR: {e}")

            # Show what backend APIs were hit
            interesting = [r for r in recorded
                           if "set.or.th" in r["url"]
                           or "settrade" in r["url"]]
            print(f"\nFound {len(interesting)} set.or.th JSON endpoints:")
            for r in interesting[:25]:
                u = r["url"]
                if len(u) > 140:
                    u = u[:120] + "...[TRUNC]"
                print(f"  [{r['status']}] {r['method']} {u}")

            # Save full capture
            dump_path = OUT / f"{name}_network.json"
            dump_path.write_text(
                json.dumps(recorded, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Full capture: {dump_path}")

            page.close()

        browser.close()


if __name__ == "__main__":
    run()
