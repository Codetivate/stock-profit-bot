"""Re-parse financials.json from existing local raw zips — no network.

Useful after a parser bugfix: rebuilds every affected symbol's
processed/financials.json purely from what's already in data/raw/,
skipping SET news search and zip download.

Usage:
    python -m src.cli.reparse_financials KBANK BBL SCB ...
    python -m src.cli.reparse_financials --all         # every symbol with raw data
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from parsers.parse_set_zip import parse_zip
from src.cli.ingest_financials import (
    PROCESSED_ROOT,
    _load_company_meta,
    compute_standalone_quarters,
)

RAW_ROOT = Path("data/raw")


def _reparse_one(symbol: str) -> dict | None:
    sym_root = RAW_ROOT / symbol / "financials"
    if not sym_root.exists():
        print(f"  {symbol}: no raw data, skipping")
        return None

    parse_rows: list[dict] = []
    staged: list[Path] = []
    for year_dir in sorted(sym_root.iterdir()):
        if not year_dir.is_dir():
            continue
        for period_dir in sorted(year_dir.iterdir()):
            if not period_dir.is_dir():
                continue
            zip_path = period_dir / "source.zip"
            meta_path = period_dir / "metadata.json"
            if not zip_path.exists() or not meta_path.exists():
                continue
            staged.append(zip_path)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            try:
                fd = parse_zip(str(zip_path), symbol=symbol)
            except Exception as e:
                print(f"    ✗ parse error {meta['thai_year']} {meta['period']}: {e}")
                continue
            if not fd or (
                fd.shareholder_profit is None and fd.shareholder_profit_cum is None
            ):
                print(f"    ✗ parse miss: {meta['thai_year']} {meta['period']}")
                continue
            rel_raw = zip_path.relative_to(RAW_ROOT).as_posix()
            # Prefer the year parsed from the XLSX's fiscal end date over
            # the headline-derived year from the file path. AEONTS et al.
            # use a non-Dec fiscal year and label the filing by the start
            # year (``ประจำปี 2565``), but the XLSX's ``สำหรับปีสิ้นสุด
            # 28 กุมภาพันธ์ 2566`` line — and SET's company-highlight
            # API — both key by the end year (2566). Only override when
            # the parser found a confident year and it's within a sane
            # +/-1 window of the headline year (filings labelled "FY"
            # cover the END of the period, never more than a year off).
            row_year = meta["thai_year"]
            if (
                fd.year
                and 2540 <= fd.year <= 2600
                and abs(fd.year - meta["thai_year"]) <= 1
            ):
                row_year = fd.year
            parse_rows.append({
                "symbol": symbol,
                "thai_year": row_year,
                "period": meta["period"],
                "shareholder_profit": fd.shareholder_profit,
                "shareholder_profit_prior": fd.shareholder_profit_prior,
                "shareholder_profit_cum": fd.shareholder_profit_cum,
                "shareholder_profit_cum_prior": fd.shareholder_profit_cum_prior,
                "cum_months": fd.cum_months,
                "primary_months": fd.primary_months,
                "revenue": fd.revenue,
                "net_profit": fd.net_profit,
                "eps": fd.eps,
                "filing_date": meta["filing_date"],
                "news_id": meta["news_id"],
                "raw_path": rel_raw,
                "sha256": meta["sha256"],
                "ingested_at": meta["ingested_at"],
            })

    if not parse_rows:
        print(f"  {symbol}: no parseable zips")
        return None

    # Dedupe by (thai_year, period), keeping the latest filing_date —
    # the reparse loop picks up both pre- and post-audit drafts when
    # both are staged locally; post-audit (later date) should win.
    latest_per_period: dict = {}
    for row in parse_rows:
        key = (row["thai_year"], row["period"])
        cur = latest_per_period.get(key)
        if cur is None or (row.get("filing_date") or "") > (cur.get("filing_date") or ""):
            latest_per_period[key] = row
    parse_rows = list(latest_per_period.values())

    quarterly = compute_standalone_quarters(parse_rows)
    proc_dir = PROCESSED_ROOT / symbol
    proc_dir.mkdir(parents=True, exist_ok=True)
    out_path = proc_dir / "financials.json"

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

    years = sorted(quarterly.keys())
    print(f"  {symbol}: {len(parse_rows)} filings → "
          f"{len(years)} years "
          f"({years[0] if years else '-'}–{years[-1] if years else '-'})")
    return payload


def _all_symbols() -> List[str]:
    return sorted([p.name for p in RAW_ROOT.iterdir()
                   if p.is_dir() and (p / "financials").exists()])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--all", action="store_true", help="Reparse every symbol with raw data")
    args = ap.parse_args()

    symbols = _all_symbols() if args.all else args.symbols
    if not symbols:
        ap.error("provide symbols or --all")

    print(f"Reparsing {len(symbols)} symbol(s)…")
    for s in symbols:
        _reparse_one(s.upper())


if __name__ == "__main__":
    main()
