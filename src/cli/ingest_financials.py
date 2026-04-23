"""Ingest all financial statements for one symbol.

Pipeline:
    1. search SET news for financial_statement items (5-year max from SET).
    2. For each: grab zip URL from detail page, download to data/raw/.
    3. Parse the XLSX (net profit attributable to shareholders).
    4. Compute standalone quarterly values (Q2 = H1 - Q1, etc.).
    5. Emit data/processed/{SYMBOL}/financials.json per the v1 schema,
       including a provenance `sources` manifest.
    6. Diff against the previous file and print unchanged/changed/new.

Usage:
    python -m src.cli.ingest_financials CPALL
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from parsers.parse_set_zip import parse_zip  # existing XLSX parser
from src.ingest.browser import SetSession
from src.ingest.set_api import extract_zip_urls, search_news
from src.ingest.zip_downloader import IngestedFiling, download_filing, parse_headline


PROCESSED_ROOT = Path("data/processed")
REFERENCE_COMPANIES = Path("reference/companies.json")


def _load_company_meta(symbol: str) -> dict:
    if not REFERENCE_COMPANIES.exists():
        return {}
    data = json.loads(REFERENCE_COMPANIES.read_text(encoding="utf-8"))
    return (data.get("companies") or {}).get(symbol, {})


def _is_financial_statement(headline: str) -> bool:
    return "งบการเงิน" in (headline or "")


def _is_amendment(headline: str) -> bool:
    """Skip corrections/clarifications — they share period with the original
    and would race to overwrite metadata. We ingest the canonical filing."""
    h = headline or ""
    return any(k in h for k in ("คำชี้แจง", "แก้ไข", "ชี้แจงเพิ่มเติม"))


def compute_standalone_quarters(
    sources: List[dict],
) -> Dict[int, Dict[str, Optional[float]]]:
    """Turn a list of {year, period, shareholder_profit} into year→Q1..Q4.

    SET XLSX filings report *current quarter* as the first numeric column
    for Q1/H1/9M reports — so the value the parser hands us for H1 is
    already standalone Q2, not the cumulative H1 sum. Only the annual
    (FY) filing reports the full-year total; Q4 is back-computed from it.
    """
    by_year: Dict[int, Dict[str, float]] = {}
    for r in sources:
        sp = r.get("shareholder_profit")
        if sp is None:
            continue
        y = r["thai_year"]
        p = r["period"]
        by_year.setdefault(y, {})[p] = sp

    out: Dict[int, Dict[str, Optional[float]]] = {}
    for y, periods in by_year.items():
        q1 = periods.get("Q1")
        q2 = periods.get("H1")     # the 3-month figure in the H1 report
        q3 = periods.get("9M")     # the 3-month figure in the 9M report
        fy = periods.get("FY")     # full-year total from the annual filing
        q4 = None
        if all(v is not None for v in (fy, q1, q2, q3)):
            q4 = fy - q1 - q2 - q3
        out[y] = {"Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4}
    return out


def ingest_symbol(
    symbol: str,
    *,
    years_back: int = 6,
    today: Optional[date] = None,
    session: Optional[SetSession] = None,
) -> dict:
    """Ingest financials for one symbol. If `session` is provided, reuse it
    (needed when called from a caller that already holds a Playwright session,
    since the sync API forbids nesting)."""
    today = today or date.today()
    from_date = today - timedelta(days=365 * years_back)

    print(f"\n=== Ingesting {symbol}  ·  {from_date} → {today} ===\n")

    staged: List[IngestedFiling] = []
    parse_rows: List[dict] = []

    session_ctx = (
        nullcontext(session) if session is not None
        else SetSession(warm_symbol=symbol)
    )
    with session_ctx as session:
        print("[1/4] Fetching news feed…")
        news = search_news(session, symbol, from_date, today, today=today)
        fin_items = [
            n for n in news
            if _is_financial_statement(n.headline) and not _is_amendment(n.headline)
            and parse_headline(n.headline) is not None
        ]
        print(f"      {len(news)} total news, {len(fin_items)} financial statements")

        print("\n[2/4] Downloading zips + extracting XLSX…")
        for i, item in enumerate(fin_items, 1):
            try:
                urls = extract_zip_urls(session, item.url)
                if not urls:
                    print(f"      [{i:2d}/{len(fin_items)}] ✗ no zip: {item.headline[:60]}")
                    continue
                filing = download_filing(
                    symbol=symbol,
                    zip_url=urls[0],
                    news_id=item.news_id,
                    headline=item.headline,
                    news_datetime=item.datetime,
                )
                staged.append(filing)
                size_kb = filing.zip_path.stat().st_size // 1024
                print(f"      [{i:2d}/{len(fin_items)}] ✓ "
                      f"{filing.key.thai_year} {filing.key.period}  "
                      f"({size_kb}KB) {filing.headline[:50]}")
            except Exception as e:
                print(f"      [{i:2d}/{len(fin_items)}] ERROR: {e}")

    print(f"\n[3/4] Parsing {len(staged)} XLSX files…")
    for f in staged:
        try:
            fd = parse_zip(str(f.zip_path), symbol=symbol)
            if not fd or fd.shareholder_profit is None:
                print(f"      ✗ parse miss: {f.key.thai_year} {f.key.period}")
                continue
            parse_rows.append({
                "thai_year": f.key.thai_year,
                "period": f.key.period,
                "shareholder_profit": fd.shareholder_profit,
                "revenue": fd.revenue,
                "net_profit": fd.net_profit,
                "eps": fd.eps,
                "filing_date": f.filing_date,
                "news_id": f.news_id,
                "raw_path": str(f.zip_path.relative_to(Path("data/raw"))).replace("\\", "/"),
                "sha256": f.sha256,
                "ingested_at": json.loads(f.metadata_path.read_text(encoding="utf-8"))["ingested_at"],
            })
            print(f"      ✓ {f.key.thai_year} {f.key.period}  "
                  f"shareholder_profit={fd.shareholder_profit:,.2f} MB")
        except Exception as e:
            print(f"      ✗ parse error {f.key.thai_year} {f.key.period}: {e}")

    print(f"\n[4/4] Computing standalone quarterly + emitting financials.json…")
    quarterly = compute_standalone_quarters(parse_rows)

    # Load any existing file for comparison
    proc_dir = PROCESSED_ROOT / symbol
    proc_dir.mkdir(parents=True, exist_ok=True)
    out_path = proc_dir / "financials.json"
    previous = {}
    if out_path.exists():
        previous = json.loads(out_path.read_text(encoding="utf-8"))
    previous_quarterly = {
        int(y): q for y, q in (previous.get("quarterly_history") or {}).items()
    }

    meta = _load_company_meta(symbol)
    payload = {
        "schema_version": 1,
        "symbol": symbol,
        "company_code": meta.get("company_code"),
        "company_name_en": meta.get("name_en"),
        "company_name_th": meta.get("name_th"),
        "updated_at": max((r["filing_date"] for r in parse_rows), default=None),
        "quarterly_history": {
            str(y): quarterly[y] for y in sorted(quarterly.keys())
        },
        "sources": [
            {
                "year": r["thai_year"],
                "period": r["period"],
                "filing_date": r["filing_date"],
                "news_id": r["news_id"],
                "raw_path": r["raw_path"],
                "sha256": r["sha256"],
                "ingested_at": r["ingested_at"],
                "shareholder_profit": r["shareholder_profit"],
            }
            for r in sorted(parse_rows, key=lambda x: (x["thai_year"], x["period"]))
        ],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"      Wrote {out_path}")

    # Diff
    print("\n=== Diff vs previous financials.json ===")
    all_years = sorted(set(quarterly.keys()) | set(previous_quarterly.keys()))
    unchanged = changed = new = 0
    for y in all_years:
        new_qs = quarterly.get(y, {})
        old_qs = previous_quarterly.get(y, {})
        for q in ("Q1", "Q2", "Q3", "Q4"):
            nv = new_qs.get(q)
            ov = old_qs.get(q)
            if ov is None and nv is None:
                continue
            if ov is None:
                new += 1
                print(f"  +  {y} {q}: {nv}")
            elif nv is None:
                changed += 1
                print(f"  ?  {y} {q}: was {ov}, now MISSING")
            elif abs(nv - ov) < 0.01:
                unchanged += 1
            else:
                changed += 1
                delta = nv - ov
                print(f"  ≠  {y} {q}: {ov:,.2f} → {nv:,.2f}  (Δ {delta:+,.2f})")
    print(f"\n  Summary: {unchanged} unchanged, {changed} changed, {new} new")

    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--today", help="Override 'today' (YYYY-MM-DD) for reproducibility")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    ingest_symbol(args.symbol.upper(), years_back=args.years, today=today)


if __name__ == "__main__":
    main()
