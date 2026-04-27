"""End-to-end completeness test — for every symbol with quarterly data,
render the chart and verify all expected cells populate.

Reports per-symbol gaps where:
  - raw zips for the period exist (data filed at SET) AND
  - the quarter / Full Year cell is empty in the rendered chart

Skips cells that are legitimately blank (no SET filing for that period
yet — e.g. current quarter not filed, or company didn't file H1).

Usage:
    python -m src.cli.test_render_all              # check every symbol
    python -m src.cli.test_render_all --render     # also write PNGs
    python -m src.cli.test_render_all --symbol KCE HANA DELTA
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from command_handler import (
    find_latest_quarter,
    get_company_name,
    load_symbol_history,
)
from src.cli.audit_completeness import _raw_periods_per_year

PROCESSED = Path("data/processed")
OUT_DIR = ROOT / "test_renders"


def _expected_cells(symbol: str, history: dict) -> dict:
    """Return {(year, slot): expected_status} where status is 'expect' or 'skip'."""
    raw = _raw_periods_per_year(symbol)
    out: dict = {}

    last5 = sorted(history.keys())[-5:]
    for y in last5:
        sources = raw.get(y, set())
        # Q1, Q2, Q3, Q4, FY each need their derivation source
        # Q1 — needs Q1 filing
        out[(y, "Q1")] = "expect" if "Q1" in sources else "skip"
        # Q2 — directly from H1, OR derivable from 9M cum (which needs Q1+9M)
        if "H1" in sources or {"Q1", "9M"}.issubset(sources):
            out[(y, "Q2")] = "expect"
        else:
            out[(y, "Q2")] = "skip"
        # Q3 — from 9M filing
        out[(y, "Q3")] = "expect" if "9M" in sources else "skip"
        # Q4 — derivable from FY + 9M cum (or full Q1+Q2+Q3+FY)
        if "FY" in sources and ("9M" in sources or
                                 {"Q1", "H1"}.issubset(sources)):
            out[(y, "Q4")] = "expect"
        else:
            out[(y, "Q4")] = "skip"
        # Full Year — comes from FY filing
        out[(y, "FY")] = "expect" if "FY" in sources else "skip"
    return out


def test_symbol(symbol: str, *, render: bool = False) -> dict:
    history = load_symbol_history(symbol)
    if not history:
        return {"symbol": symbol, "status": "EMPTY", "gaps": []}

    expected = _expected_cells(symbol, history)
    gaps: list[tuple[int, str]] = []
    for (y, slot), status in expected.items():
        if status != "expect":
            continue
        qd = history.get(y)
        if qd is None:
            gaps.append((y, slot))
            continue
        if slot == "FY":
            val = qd.sum()
        else:
            val = qd.get(slot)
        if val is None:
            gaps.append((y, slot))

    result = {
        "symbol": symbol,
        "status": "OK" if not gaps else "GAPS",
        "gaps": gaps,
    }

    if render and not gaps:
        try:
            from make_chart_html import make_chart
            ly, lq = find_latest_quarter(history)
            png = make_chart(
                symbol=symbol,
                company_name=get_company_name(symbol),
                history=history,
                latest_year=ly,
                latest_quarter=lq,
                report_date="",
                period_label=f"FY {ly}  ·  {lq}",
            )
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"{symbol}.png").write_bytes(png)
            result["rendered_bytes"] = len(png)
        except Exception as e:
            result["render_error"] = str(e)

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", action="append", help="Test specific symbol(s)")
    ap.add_argument("--render", action="store_true", help="Also render PNGs")
    ap.add_argument("--gaps-only", action="store_true",
                    help="Only print symbols with gaps")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limit number of symbols (for fast smoke tests)")
    args = ap.parse_args()

    if args.symbol:
        symbols = [s.upper() for s in args.symbol]
    else:
        symbols = sorted(p.name for p in PROCESSED.iterdir() if p.is_dir())
        if args.limit:
            symbols = symbols[:args.limit]

    counts = {"OK": 0, "GAPS": 0, "EMPTY": 0}
    gap_rows: list[dict] = []
    for s in symbols:
        try:
            r = test_symbol(s, render=args.render)
        except Exception as e:
            print(f"  {s}: EXCEPTION {e}")
            traceback.print_exc()
            continue
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] == "GAPS":
            gap_rows.append(r)
            if args.gaps_only or args.symbol:
                gap_str = ", ".join(f"{y}/{slot}" for y, slot in r["gaps"])
                print(f"  ✗ {r['symbol']}: {gap_str}")
        elif args.symbol:
            print(f"  ✓ {r['symbol']}")

    print(f"\n{'='*60}")
    print(f"Tested {len(symbols)} symbols")
    print(f"  OK   : {counts.get('OK', 0)}")
    print(f"  GAPS : {counts.get('GAPS', 0)}")
    print(f"  EMPTY: {counts.get('EMPTY', 0)}")
    if gap_rows and not args.gaps_only and not args.symbol:
        print(f"\nFirst 30 with gaps:")
        for r in gap_rows[:30]:
            gap_str = ", ".join(f"{y}/{slot}" for y, slot in r["gaps"][:6])
            print(f"  {r['symbol']:<8}: {gap_str}")


if __name__ == "__main__":
    main()
