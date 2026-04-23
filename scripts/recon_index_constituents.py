"""Discover SET/mai index constituent APIs by exercising the stocks-in-the-
index pages and capturing their backend calls.

Runs Playwright, navigates to each index page, captures any /api/set/*
response with a 200, and dumps them for inspection.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


OUT = Path("scripts/recon_out")
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = {
    "set50": "https://www.set.or.th/th/market/get-quote/composite/stocks-in-the-set50",
    "set100": "https://www.set.or.th/th/market/get-quote/composite/stocks-in-the-set100",
    "mai":   "https://www.set.or.th/th/market/get-quote/composite/stocks-in-the-mai",
}


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="th-TH")

        for name, url in TARGETS.items():
            print(f"\n{'='*60}\n{name}\n{'='*60}")
            page = context.new_page()
            captures = []

            def on_response(resp, target=captures):
                u = resp.url
                if "/api/set/" in u and resp.status == 200:
                    try:
                        ct = resp.headers.get("content-type", "")
                        if "json" in ct:
                            target.append({
                                "url": u,
                                "status": resp.status,
                                "body_preview": resp.text()[:300],
                            })
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(8_000)
            except Exception as e:
                print(f"  nav error: {e}")

            # Only keep interesting, unique API calls
            seen = {}
            for c in captures:
                # Strip query for grouping
                key = c["url"].split("?")[0]
                seen.setdefault(key, c)

            print(f"\n{len(seen)} unique endpoint(s) hit:")
            for key, c in seen.items():
                print(f"  {c['url']}")
                body = c["body_preview"].replace("\n", " ")
                print(f"    → {body[:200]}")
                print()

            (OUT / f"{name}_endpoints.json").write_text(
                json.dumps(list(seen.values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            page.close()

        browser.close()


if __name__ == "__main__":
    run()
