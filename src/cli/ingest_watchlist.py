"""Bulk-ingest every symbol in the watchlist.

Walks reference/set50.json (or a list passed on the CLI), opens a single
Playwright session, and calls ingest_symbol + ingest_symbol_news for each
symbol. Idempotent: re-running skips symbols that already have a
processed/financials.json with a non-empty sources[] manifest, unless
--force is passed.

Usage:
    python -m src.cli.ingest_watchlist                         # SET50 default
    python -m src.cli.ingest_watchlist --list set100           # another list
    python -m src.cli.ingest_watchlist --symbol PTT --symbol AOT
    python -m src.cli.ingest_watchlist --force                 # re-ingest all
    python -m src.cli.ingest_watchlist --skip-news             # financials only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.cli.ingest_financials import ingest_symbol
from src.cli.ingest_news import ingest_symbol_news
from src.ingest.browser import SetSession


REFERENCE_DIR = Path("reference")
PROCESSED_DIR = Path("data/processed")


def _load_list(list_name: str) -> List[str]:
    path = REFERENCE_DIR / f"{list_name}.json"
    if not path.exists():
        raise SystemExit(f"watchlist not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("symbols") or [])


def _already_ingested(symbol: str) -> bool:
    """Cheap check: financials.json exists and has at least one source."""
    path = PROCESSED_DIR / symbol / "financials.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data.get("sources", [])) > 0
    except Exception:
        return False


def ingest_watchlist(
    symbols: List[str],
    *,
    years_back: int = 6,
    skip_news: bool = False,
    force: bool = False,
    delay_between_symbols: float = 2.0,
) -> dict:
    today = date.today()
    summary = {
        "attempted": 0,
        "financials_ok": 0,
        "news_ok": 0,
        "skipped": 0,
        "errors": [],
    }

    print(f"\n{'='*60}")
    print(f"  Bulk ingest  ·  {len(symbols)} symbols  ·  {years_back}y history")
    print(f"  force={force}  skip_news={skip_news}")
    print(f"{'='*60}\n")

    # One browser session for all symbols — Incapsula cookie warms once.
    with SetSession(warm_symbol=symbols[0]) as session:
        for i, symbol in enumerate(symbols, 1):
            summary["attempted"] += 1
            prefix = f"[{i:2d}/{len(symbols)}] {symbol:8s}"

            if not force and _already_ingested(symbol):
                print(f"{prefix}  ✓ already ingested (pass --force to redo)")
                summary["skipped"] += 1
                continue

            # Financials
            try:
                ingest_symbol(
                    symbol,
                    years_back=years_back,
                    today=today,
                    session=session,
                )
                summary["financials_ok"] += 1
                print(f"{prefix}  ✓ financials ingested")
            except Exception as e:
                traceback.print_exc()
                summary["errors"].append(f"{symbol}: financials — {e}")
                print(f"{prefix}  ✗ financials FAILED: {e}")
                # Continue to news even if financials failed; they're independent

            # News
            if not skip_news:
                try:
                    ingest_symbol_news(
                        symbol,
                        years_back=years_back,
                        today=today,
                        session=session,
                    )
                    summary["news_ok"] += 1
                    print(f"{prefix}  ✓ news ingested")
                except Exception as e:
                    summary["errors"].append(f"{symbol}: news — {e}")
                    print(f"{prefix}  ✗ news FAILED: {e}")

            # Polite pause so we don't hammer SET
            if i < len(symbols):
                time.sleep(delay_between_symbols)

    # Summary
    print(f"\n{'='*60}")
    print("  Bulk ingest summary")
    print(f"{'='*60}")
    print(f"  Attempted     : {summary['attempted']}")
    print(f"  Financials OK : {summary['financials_ok']}")
    print(f"  News OK       : {summary['news_ok']}")
    print(f"  Skipped       : {summary['skipped']}")
    print(f"  Errors        : {len(summary['errors'])}")
    if summary["errors"]:
        print("\n  Error details:")
        for err in summary["errors"]:
            print(f"    - {err}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="set50",
                    help="Which reference/<list>.json to load (default set50)")
    ap.add_argument("--symbol", action="append",
                    help="Override list with explicit symbol(s). Repeat.")
    ap.add_argument("--years", type=int, default=6,
                    help="How many years of history per symbol (default 6)")
    ap.add_argument("--skip-news", action="store_true",
                    help="Only ingest financials, skip announcements.json.")
    ap.add_argument("--force", action="store_true",
                    help="Re-ingest even symbols with an existing "
                         "financials.json. Default is to skip them.")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="Seconds to pause between symbols (default 2)")
    args = ap.parse_args()

    symbols = args.symbol or _load_list(args.list)
    if not symbols:
        raise SystemExit("No symbols to process.")

    ingest_watchlist(
        [s.upper() for s in symbols],
        years_back=args.years,
        skip_news=args.skip_news,
        force=args.force,
        delay_between_symbols=args.delay,
    )


if __name__ == "__main__":
    main()
