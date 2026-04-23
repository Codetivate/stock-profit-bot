"""Recon script — inspects SET pages to map their DOM for the scraper.

Not production code. Saves HTML snapshots + console logs for each target URL
so we can design selectors based on what the page actually renders.

Usage:
    python scripts/recon_set_pages.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


SYMBOL = "CPALL"
OUT_DIR = Path("scripts/recon_out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TARGETS = {
    "news_center": (
        "https://www.set.or.th/th/market/news-and-alert/news"
        "?source=company&securityType=S&fromDate=2021-04-23&toDate=2026-04-23"
    ),
    "stock_news": f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/news",
    "rights_benefits": f"https://www.set.or.th/th/market/product/stock/quote/{SYMBOL}/rights-benefits",
}


def recon():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            locale="th-TH",
            viewport={"width": 1440, "height": 900},
        )

        for name, url in TARGETS.items():
            print(f"\n=== {name} ===")
            print(f"URL: {url}")
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                # Give React time to fetch + render content
                page.wait_for_timeout(8000)
                html = page.content()
                title = page.title()
                print(f"Title: {title}")
                print(f"HTML size: {len(html):,} bytes")

                # Save
                out = OUT_DIR / f"{name}.html"
                out.write_text(html, encoding="utf-8")
                print(f"Saved: {out}")

                # Screenshot for visual sanity
                shot = OUT_DIR / f"{name}.png"
                page.screenshot(path=str(shot), full_page=False)

                # Some quick probes
                cpall_count = html.count("CPALL")
                ngod_count = html.count("งบการเงิน")
                dividend_count = html.count("ปันผล")
                zip_count = html.count("weblink.set.or.th")
                print(f"  CPALL mentions: {cpall_count}")
                print(f"  งบการเงิน mentions: {ngod_count}")
                print(f"  ปันผล mentions: {dividend_count}")
                print(f"  weblink.set.or.th URLs: {zip_count}")
            except Exception as e:
                print(f"  ERROR: {e}")
            finally:
                page.close()

        browser.close()


if __name__ == "__main__":
    recon()
