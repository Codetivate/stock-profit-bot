"""Bulk-ingest financials for many symbols using one shared SET session.

Re-uses a single Playwright browser instance across symbols — fetch
overhead drops from ~30s/symbol (cold-start session each time) to
~3-5s/symbol (warm session).

Auto-targets symbols that are EMPTY or have raw zips matching the
audit's "DERIVABLE_BUT_MISSING" / "PARSE_FAIL" buckets, unless the
caller passes an explicit list.

Usage:
    python -m src.cli.bulk_ingest                         # auto: empty + gappy
    python -m src.cli.bulk_ingest --empty-only            # only EMPTY symbols
    python -m src.cli.bulk_ingest --symbol KCE HANA       # explicit list
    python -m src.cli.bulk_ingest --watchlist set50       # ingest one watchlist
    python -m src.cli.bulk_ingest --years 6 --max 50      # cap batch size
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.cli.audit_completeness import audit_symbol
from src.cli.ingest_financials import ingest_symbol
from src.ingest.browser import SetSession


PROCESSED = Path("data/processed")
REFERENCE = Path("reference")


def _all_symbols() -> List[str]:
    return sorted(p.name for p in PROCESSED.iterdir() if p.is_dir())


def _watchlist_symbols(name: str) -> List[str]:
    fp = REFERENCE / f"{name}.json"
    data = json.loads(fp.read_text(encoding="utf-8"))
    return list(data.get("symbols") or [])


def _filter_targets(empty_only: bool) -> List[str]:
    """Return symbols that need (re-)ingest based on the audit."""
    out: list[str] = []
    for s in _all_symbols():
        r = audit_symbol(s)
        if r["status"] == "EMPTY":
            out.append(s)
        elif not empty_only and r["status"] == "GAPS":
            out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", action="append",
                    help="Explicit list (overrides auto-detection)")
    ap.add_argument("--watchlist", help="Use a reference/<name>.json watchlist")
    ap.add_argument("--empty-only", action="store_true",
                    help="Only ingest symbols with empty quarterly_history")
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--max", type=int, default=None,
                    help="Cap batch size (use to spread bulk runs)")
    ap.add_argument("--skip", type=int, default=0,
                    help="Skip the first N targets (resumable batch)")
    args = ap.parse_args()

    if args.symbol:
        targets = [s.upper() for s in args.symbol]
    elif args.watchlist:
        targets = _watchlist_symbols(args.watchlist)
    else:
        targets = _filter_targets(empty_only=args.empty_only)

    if args.skip:
        targets = targets[args.skip:]
    if args.max:
        targets = targets[:args.max]

    if not targets:
        print("Nothing to do — every symbol's data is already complete.")
        return

    print(f"Bulk ingest  ·  {len(targets)} symbols  ·  years={args.years}")

    started = time.monotonic()
    ok = err = 0
    today = date.today()

    with SetSession(warm_symbol=targets[0]) as session:
        for i, sym in enumerate(targets, 1):
            t0 = time.monotonic()
            try:
                ingest_symbol(sym, years_back=args.years,
                              today=today, session=session)
                ok += 1
                print(f"\n[{i:>3}/{len(targets)}] ✓ {sym}  "
                      f"({time.monotonic()-t0:.1f}s)\n")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                err += 1
                print(f"\n[{i:>3}/{len(targets)}] ✗ {sym}: {e}\n")
                traceback.print_exc()

    elapsed = time.monotonic() - started
    print(f"\n{'='*60}")
    print(f"Bulk ingest done in {elapsed/60:.1f} min "
          f"({ok} ok, {err} errors, {ok+err}/{len(targets)})")


if __name__ == "__main__":
    main()
