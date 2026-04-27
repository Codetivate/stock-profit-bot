"""Audit every symbol's quarterly_history for completeness.

Reads ``data/processed/*/financials.json`` and reports, for each symbol:
  - latest year covered
  - whether each quarter (Q1..Q4) and full-year total in the latest 5
    years are populated
  - the periods present in raw filings (so we can tell apart "no data
    fetched yet" from "filed but parser failed")

A quarter is considered legitimately blank if the raw filing for its
period simply isn't in data/raw/ — that means SET hasn't published
it yet (current year) or our ingester never reached it.

Usage:
    python -m src.cli.audit_completeness
    python -m src.cli.audit_completeness --gaps-only
    python -m src.cli.audit_completeness --symbol KCE HANA DELTA
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROCESSED = Path("data/processed")
RAW = Path("data/raw")


def _load_qh(symbol: str) -> Optional[dict]:
    fp = PROCESSED / symbol / "financials.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _raw_periods_per_year(symbol: str) -> dict[int, set[str]]:
    """Return {thai_year: {periods}} for filings staged in data/raw/."""
    out: dict[int, set[str]] = defaultdict(set)
    base = RAW / symbol / "financials"
    if not base.exists():
        return out
    for year_dir in base.iterdir():
        if not year_dir.is_dir():
            continue
        try:
            y = int(year_dir.name)
        except ValueError:
            continue
        for period_dir in year_dir.iterdir():
            if (period_dir / "source.zip").exists():
                out[y].add(period_dir.name)
    return out


def audit_symbol(symbol: str) -> dict:
    """Return a per-symbol audit row."""
    data = _load_qh(symbol)
    if not data:
        return {"symbol": symbol, "status": "NO_FILE"}

    qh = data.get("quarterly_history") or {}
    if not qh:
        return {"symbol": symbol, "status": "EMPTY",
                "raw_periods": _raw_periods_per_year(symbol)}

    raw = _raw_periods_per_year(symbol)
    years = sorted(qh.keys(), key=int)[-5:]  # last 5 years

    # For each (year, quarter) check if it's filled or "legitimately
    # blank" because its source filing isn't staged yet.
    gaps: list[tuple[int, str, str]] = []   # (year, slot, reason)
    for y_str in years:
        y = int(y_str)
        q = qh[y_str]
        sources_for_year = raw.get(y, set())
        # Q1 → needs Q1 filing
        for slot, needs in [
            ("Q1", "Q1"),
            ("Q2", "H1"),       # also derivable from 9M cum + Q1 + Q3
            ("Q3", "9M"),
            ("Q4", "FY"),       # also derivable from FY + 9M cum
            ("FullYear", "FY"),
        ]:
            val = q.get(slot)
            if val is None:
                # Determine why
                if needs not in sources_for_year:
                    # If Q2 missing but BOTH Q1 and 9M sources present,
                    # the parser should have derived it from 9M cum.
                    if slot == "Q2" and {"Q1", "9M"}.issubset(sources_for_year):
                        gaps.append((y, slot, "DERIVABLE_BUT_MISSING"))
                    elif slot == "Q4" and {"9M", "FY"}.issubset(sources_for_year):
                        gaps.append((y, slot, "DERIVABLE_BUT_MISSING"))
                    else:
                        gaps.append((y, slot, "NO_SOURCE"))
                else:
                    gaps.append((y, slot, "PARSE_FAIL"))

    return {
        "symbol": symbol,
        "status": "OK" if not gaps else "GAPS",
        "years": years,
        "gaps": gaps,
        "raw_periods": {y: sorted(p) for y, p in raw.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", action="append", help="Audit specific symbol(s)")
    ap.add_argument("--gaps-only", action="store_true",
                    help="Only print symbols with gaps")
    ap.add_argument("--derivable-only", action="store_true",
                    help="Only print rows where the parser SHOULD have filled "
                         "but didn't (raw zips present, but quarterly missing)")
    args = ap.parse_args()

    if args.symbol:
        symbols = [s.upper() for s in args.symbol]
    else:
        symbols = sorted(p.name for p in PROCESSED.iterdir() if p.is_dir())

    rows = [audit_symbol(s) for s in symbols]

    # Summary counts
    counts = defaultdict(int)
    derivable_misses: list[tuple[str, list]] = []
    for r in rows:
        counts[r["status"]] += 1
        if r["status"] == "GAPS":
            d = [g for g in r["gaps"] if g[2] == "DERIVABLE_BUT_MISSING"]
            if d:
                derivable_misses.append((r["symbol"], d))

    print(f"\nAudited {len(rows)} symbols")
    for k, v in counts.items():
        print(f"  {k:<12}: {v}")

    print(f"\n{len(derivable_misses)} symbols have raw zips for the period "
          f"but the parser left the quarter blank — those are real bugs.")
    for sym, gaps in derivable_misses[:30]:
        gap_str = ", ".join(f"{y}/{slot}" for y, slot, _ in gaps)
        print(f"  {sym}: {gap_str}")

    if args.derivable_only:
        return

    if args.gaps_only or args.symbol:
        for r in rows:
            if r["status"] == "OK":
                continue
            print(f"\n{r['symbol']}  [{r['status']}]")
            if r.get("gaps"):
                for y, slot, reason in r["gaps"]:
                    print(f"   {y} {slot:<8} {reason}")


if __name__ == "__main__":
    main()
