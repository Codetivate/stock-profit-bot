"""Capture the exact headers the browser uses when calling the news-center
API, so we can replicate them in our client and unlock the single-call
"watch the whole market" strategy.

Previous attempt on /api/cms/v1/news/set returned 401 — something in
the browser's auth path wasn't reproduced.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


OUT = Path("scripts/recon_out")
OUT.mkdir(parents=True, exist_ok=True)

TARGET_PATH = "/api/cms/v1/news/set"


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="th-TH")
        page = context.new_page()

        captures = []

        def on_request(req):
            if TARGET_PATH in req.url:
                captures.append({
                    "url": req.url,
                    "method": req.method,
                    "headers": dict(req.headers),
                })

        page.on("request", on_request)

        url = ("https://www.set.or.th/th/market/news-and-alert/news"
               "?source=company&securityType=S"
               "&fromDate=2021-04-23&toDate=2026-04-23")
        print(f"Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(10_000)

        browser.close()

    print(f"\nCaptured {len(captures)} request(s) to {TARGET_PATH}:\n")
    for i, c in enumerate(captures, 1):
        print(f"── request #{i} ──")
        print(f"URL: {c['url']}")
        print(f"Method: {c['method']}")
        print("Headers:")
        for k, v in c["headers"].items():
            # Truncate long cookie/token headers so output is readable
            v_display = v if len(v) < 120 else v[:120] + "...[TRUNC]"
            print(f"  {k}: {v_display}")
        print()

    out = OUT / "news_center_request_headers.json"
    out.write_text(json.dumps(captures, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Saved full capture: {out}")


if __name__ == "__main__":
    run()
