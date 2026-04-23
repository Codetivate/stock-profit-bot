"""Probe a news detail page to find the attached zip URL."""
from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


SYMBOL = "CPALL"
OUT = Path("scripts/recon_out")

# Find a งบการเงิน news item from the already-captured API response
payload = json.loads((OUT / "stock_news_api.json").read_text(encoding="utf-8"))
items = payload.get("newsInfoList", [])

fin_items = [it for it in items if "งบการเงิน" in it.get("headline", "")]
print(f"Found {len(fin_items)} งบการเงิน items (out of {len(items)} total)\n")

if not fin_items:
    print("No งบการเงิน found; nothing to probe.")
    raise SystemExit(0)

# Take the most recent งบ
sample = fin_items[0]
print(f"Sample: id={sample['id']}  date={sample['datetime']}")
print(f"  Headline: {sample['headline']}")
print(f"  URL: {sample['url']}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(locale="th-TH")
    page = context.new_page()
    page.goto(sample["url"], wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4000)

    html = page.content()
    (OUT / "news_detail_sample.html").write_text(html, encoding="utf-8")
    print(f"\nDetail page HTML: {len(html):,} bytes")

    # Find all weblink URLs (zip attachments)
    zips = sorted(set(re.findall(r"https://weblink\.set\.or\.th/[^\"' <>]+", html)))
    print(f"\nFound {len(zips)} weblink.set.or.th URLs:")
    for z in zips[:10]:
        print(f"  {z}")

    # Also find any pdf/other attachments
    pdfs = sorted(set(re.findall(r"https://[^\"' <>]+\.pdf", html)))
    print(f"\nFound {len(pdfs)} PDF URLs:")
    for z in pdfs[:5]:
        print(f"  {z}")

    browser.close()
