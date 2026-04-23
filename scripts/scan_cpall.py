"""End-to-end scan demo for CPALL.

Fetches:
  1. Per-symbol news for the last 6 years (auto-chunked)
  2. Classifies each headline
  3. For every 'financial_statement' item, visits its detail page to grab
     the weblink.set.or.th zip URL
  4. Fetches the rights-benefits corporate-action list

Writes a JSON summary to scripts/recon_out/cpall_scan.json and prints a
grouped report.

Not production yet — once the output looks right we'll lift the logic into
src/cli/scan.py and have it persist to data/raw/ + data/processed/.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ingest.browser import SetSession
from src.ingest.set_api import (
    extract_zip_urls,
    get_corporate_actions,
    search_news,
)
from src.parse.news_classifier import classify, extract_related_symbols


SYMBOL = "CPALL"
TODAY = date(2026, 4, 23)
FROM = TODAY - timedelta(days=365 * 6)   # 6 years
OUT = ROOT / "scripts" / "recon_out" / "cpall_scan.json"


def main():
    print(f"Scanning {SYMBOL}  ·  {FROM} → {TODAY}\n")

    with SetSession(warm_symbol=SYMBOL) as session:
        print("[1/3] Fetching news feed (6 years, chunked)…")
        news = search_news(session, SYMBOL, FROM, TODAY)
        print(f"      → {len(news)} items")

        print("\n[2/3] Classifying headlines…")
        classified = []
        for n in news:
            kind = classify(n.headline)
            classified.append({
                "news_id": n.news_id,
                "date": n.date,
                "type": kind,
                "headline": n.headline,
                "url": n.url,
                "related_symbols": extract_related_symbols(n.headline, SYMBOL),
                "zip_urls": [],  # filled in below for financial_statement
            })

        # Count by type
        counts = Counter(c["type"] for c in classified)
        print("      Breakdown:")
        for kind, n in counts.most_common():
            print(f"        {kind:30s} {n:4d}")

        # Step 2b: for financial_statement items, extract zip URLs from detail pages
        fin_items = [c for c in classified if c["type"] == "financial_statement"]
        print(f"\n[2b] Extracting zip URLs from {len(fin_items)} งบการเงิน detail pages…")
        for i, item in enumerate(fin_items, 1):
            try:
                urls = extract_zip_urls(session, item["url"])
                item["zip_urls"] = urls
                mark = "✓" if urls else "✗"
                print(f"      [{i:3d}/{len(fin_items)}] {mark} {item['date']}  "
                      f"{item['headline'][:50]}  ({len(urls)} zips)")
            except Exception as e:
                item["zip_urls_error"] = str(e)
                print(f"      [{i:3d}/{len(fin_items)}] ERROR: {e}")

        print("\n[3/3] Fetching corporate actions (rights & benefits)…")
        cas = get_corporate_actions(session, SYMBOL)
        print(f"      → {len(cas)} actions")
        ca_counts = Counter(c.ca_type for c in cas)
        print("      Breakdown:")
        for t, n in ca_counts.most_common():
            print(f"        {t:10s} {n:4d}")

    # Persist
    out_payload = {
        "symbol": SYMBOL,
        "from_date": FROM.isoformat(),
        "to_date": TODAY.isoformat(),
        "news_total": len(classified),
        "news_type_counts": dict(counts),
        "news_items": classified,
        "corporate_action_total": len(cas),
        "corporate_action_type_counts": dict(ca_counts),
        "corporate_actions": [ca.__dict__ for ca in cas],
    }
    # CorporateAction contains the raw dict field → strip for readability
    for ca in out_payload["corporate_actions"]:
        ca.pop("raw", None)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2,
                              default=str), encoding="utf-8")
    print(f"\nSaved: {OUT}")

    # Brief highlight reel
    print("\n" + "=" * 70)
    print("HIGHLIGHTS")
    print("=" * 70)

    print(f"\nLatest 5 financial statements:")
    for item in fin_items[:5]:
        zn = len(item.get("zip_urls", []))
        print(f"  {item['date']}  [{zn} zip]  {item['headline']}")

    print(f"\nNon-financial news (top 10 most recent):")
    other = [c for c in classified if c["type"] != "financial_statement"]
    for item in other[:10]:
        rel = f"  →{item['related_symbols']}" if item["related_symbols"] else ""
        print(f"  {item['date']}  [{item['type']:20}]  "
              f"{item['headline'][:60]}{rel}")

    print(f"\nUpcoming / recent corporate actions:")
    for ca in cas[:10]:
        detail = ""
        if ca.dividend:
            detail = f"div={ca.dividend} บาท/หุ้น"
        elif ca.agenda:
            detail = ca.agenda[:60]
        print(f"  {ca.xdate or '?':12s}  {ca.ca_type}  {detail}")


if __name__ == "__main__":
    main()
