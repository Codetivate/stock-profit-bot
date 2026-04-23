"""Ingest classified news into data/processed/{SYMBOL}/announcements.json.

Headlines that classify as `financial_statement` or `regulatory_filing` are
*excluded* — financial statements live in financials.json, and regulatory
filings (SEC 59, F45) are procedural noise. Everything else (divestitures,
dividends, related-party deals, meetings, buybacks, …) gets persisted so
the bot can surface material non-financial news alongside the chart.

Usage:
    python -m src.cli.ingest_news CPALL
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ingest.browser import SetSession
from src.ingest.set_api import NewsItem, get_corporate_actions, search_news
from src.parse.news_classifier import classify, extract_related_symbols


PROCESSED_ROOT = Path("data/processed")

# Headline types we *don't* persist — they belong elsewhere or are noise.
EXCLUDED_TYPES = {
    "financial_statement",   # lives in financials.json
    "mgmt_discussion",       # stub document that ships with financials
    "regulatory_filing",     # routine SEC filings, forms
}


def _announcement_from(news: NewsItem, symbol: str) -> dict:
    kind = classify(news.headline)
    return {
        "news_id": news.news_id,
        "date": news.date,
        "datetime": news.datetime,
        "type": kind,
        "title": news.headline,
        "summary": None,     # phase 5+ (LLM summariser)
        "source_url": news.url,
        "subject_symbols": extract_related_symbols(news.headline, symbol),
        "raw_path": None,    # archival attachments not yet fetched
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def ingest_symbol_news(
    symbol: str,
    *,
    years_back: int = 6,
    today: date | None = None,
    session: Optional[SetSession] = None,
) -> dict:
    today = today or date.today()
    from_date = today - timedelta(days=365 * years_back)

    print(f"\n=== Ingesting news for {symbol}  ·  {from_date} → {today} ===\n")

    session_ctx = (
        nullcontext(session) if session is not None
        else SetSession(warm_symbol=symbol)
    )
    with session_ctx as session:
        print("[1/3] Fetching news feed…")
        news = search_news(session, symbol, from_date, today, today=today)
        print(f"      {len(news)} items")

        print("\n[2/3] Classifying + filtering noise…")
        kept: List[dict] = []
        excluded_count = 0
        type_counts: dict[str, int] = {}
        for n in news:
            kind = classify(n.headline)
            type_counts[kind] = type_counts.get(kind, 0) + 1
            if kind in EXCLUDED_TYPES:
                excluded_count += 1
                continue
            kept.append(_announcement_from(n, symbol))
        print(f"      Total: {len(news)}  Kept: {len(kept)}  Excluded: {excluded_count}")
        print(f"      Type counts:")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            kept_marker = " (excluded)" if t in EXCLUDED_TYPES else ""
            print(f"        {t:30s} {c:4d}{kept_marker}")

        print("\n[3/3] Fetching corporate actions (rights-benefits)…")
        cas = get_corporate_actions(session, symbol)
        print(f"      {len(cas)} actions ({', '.join(sorted({c.ca_type for c in cas}))})")

    proc_dir = PROCESSED_ROOT / symbol
    proc_dir.mkdir(parents=True, exist_ok=True)
    out_path = proc_dir / "announcements.json"

    payload = {
        "schema_version": 1,
        "symbol": symbol,
        "updated_at": datetime.now(timezone.utc).date().isoformat(),
        "announcements": sorted(kept, key=lambda a: a["datetime"], reverse=True),
        "corporate_actions": [
            {
                "ca_type": c.ca_type,
                "xdate": c.xdate,
                "record_date": c.record_date,
                "payment_date": c.payment_date,
                "meeting_date": c.meeting_date,
                "dividend": c.dividend,
                "dividend_type": c.dividend_type,
                "source_of_dividend": c.source_of_dividend,
                "agenda": c.agenda,
                "meeting_type": c.meeting_type,
            }
            for c in cas
        ],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}  ({len(kept)} announcements, {len(cas)} CAs)")

    # Highlights
    print("\n=== HIGHLIGHTS ===")
    print("\nTop 10 material announcements (most recent, non-noise):")
    for a in payload["announcements"][:10]:
        rel = f"  →{a['subject_symbols']}" if a["subject_symbols"] else ""
        print(f"  {a['date']}  [{a['type']:27s}]  {a['title'][:55]}{rel}")

    print("\nUpcoming 5 corporate actions:")
    upcoming = [c for c in payload["corporate_actions"] if c["xdate"]]
    upcoming.sort(key=lambda c: c["xdate"], reverse=True)
    for c in upcoming[:5]:
        detail = ""
        if c["dividend"]:
            detail = f"div={c['dividend']:.2f} บาท/หุ้น"
        elif c["agenda"]:
            detail = c["agenda"][:50]
        print(f"  {c['xdate']}  {c['ca_type']}  {detail}")

    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--today", help="Override 'today' (YYYY-MM-DD)")
    args = ap.parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()
    ingest_symbol_news(args.symbol.upper(), years_back=args.years, today=today)


if __name__ == "__main__":
    main()
