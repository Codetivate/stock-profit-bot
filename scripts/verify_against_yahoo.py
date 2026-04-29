"""scripts/verify_against_yahoo.py — quarterly + annual audit vs Yahoo Finance.

SET's company-highlight API only exposes annual figures (quarter == "Q9").
Yahoo Finance (free, no auth) returns the full Q1/Q2/Q3/Q4 breakdown for
Thai stocks under the `.BK` ticker suffix. That's the ground truth we
need to validate the per-quarter accuracy of our derived data.

Usage:
    python scripts/verify_against_yahoo.py CPALL TOA WAVE
    python scripts/verify_against_yahoo.py --all          # full universe
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import yfinance


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ingest.zip_downloader import safe_symbol_dir


PROCESSED_DIR = Path("data/processed")


def _gregorian_to_thai(g: int) -> int:
    return g + 543


def fetch_yahoo_quarterly(symbol: str) -> Dict[int, Dict[str, Optional[float]]]:
    """Return {thai_year: {Q1, Q2, Q3, Q4, FullYear}} (in MB).

    Yahoo returns calendar-quarter-end columns: 2025-12-31, 2025-09-30,
    2025-06-30, 2025-03-31. Each column is the standalone NET INCOME
    for that 3-month period (NOT cumulative). We rebucket by calendar
    year and quarter, sum Q1..Q4 to derive FullYear.

    Note: for Mar-fiscal-end issuers (BTS, AEONTS) the calendar bucket
    won't match SET's fiscal-year label; we still report by calendar
    year here and let the comparison logic handle the offset.
    """
    out: Dict[int, Dict[str, Optional[float]]] = {}
    t = yfinance.Ticker(f"{symbol}.BK")
    try:
        qf = t.quarterly_financials
    except Exception:
        return out
    if qf is None or qf.empty or "Net Income" not in qf.index:
        return out
    s = qf.loc["Net Income"]
    for col, val in s.items():
        try:
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue
            month = col.month
            year_g = col.year
            q_map = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}
            q = q_map.get(month)
            if q is None:
                continue
            thai = _gregorian_to_thai(year_g)
            year_data = out.setdefault(thai, {"Q1": None, "Q2": None,
                                              "Q3": None, "Q4": None})
            year_data[q] = float(val) / 1e6  # baht → MB
        except Exception:
            continue
    # FullYear = sum of Q1..Q4 only when all four are present
    for y, qs in out.items():
        vals = [qs.get(q) for q in ("Q1", "Q2", "Q3", "Q4")]
        if all(v is not None for v in vals):
            qs["FullYear"] = sum(vals)
        else:
            qs["FullYear"] = None
    return out


def load_local_quarterly(symbol: str) -> Dict[int, Dict[str, Optional[float]]]:
    path = PROCESSED_DIR / safe_symbol_dir(symbol) / "financials.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    qh = raw.get("quarterly_history", {})
    out: Dict[int, Dict[str, Optional[float]]] = {}
    for y_str, qs in qh.items():
        out[int(y_str)] = qs
    return out


def compare_symbol(symbol: str, tolerance: float = 0.05) -> dict:
    """Cross-check our quarters vs Yahoo's quarters for one symbol."""
    yh = fetch_yahoo_quarterly(symbol)
    local = load_local_quarterly(symbol)
    overlap_years = sorted(set(yh.keys()) & set(local.keys()))
    if not overlap_years:
        return {"symbol": symbol, "status": "no_overlap",
                "yahoo_years": sorted(yh.keys()),
                "local_years": sorted(local.keys())}

    mismatches: List[dict] = []
    matched = 0
    total = 0
    for y in overlap_years:
        yh_y = yh[y]
        loc_y = local[y]
        for q in ("Q1", "Q2", "Q3", "Q4", "FullYear"):
            yv = yh_y.get(q)
            lv = loc_y.get(q)
            if yv is None or lv is None:
                continue
            total += 1
            if abs(yv - lv) <= max(tolerance, abs(yv) * 0.001):
                matched += 1
            else:
                mismatches.append({
                    "year": y, "quarter": q,
                    "yahoo": round(yv, 4), "local": round(lv, 4),
                    "delta": round(lv - yv, 4),
                })
    return {
        "symbol": symbol,
        "status": "ok" if not mismatches else "mismatch",
        "matched": matched, "total": total,
        "mismatches": mismatches,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", help="symbols to audit")
    ap.add_argument("--all", action="store_true",
                    help="audit every symbol in data/processed/")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="max abs(diff) in MB to treat as a match (default 0.05)")
    ap.add_argument("--out", default=None, help="write report JSON here")
    ap.add_argument("--sleep", type=float, default=0.3,
                    help="seconds to sleep between Yahoo API calls (default 0.3)")
    args = ap.parse_args()

    if args.all:
        symbols = sorted(p.name for p in PROCESSED_DIR.iterdir()
                         if p.is_dir() and (p / "financials.json").exists())
        symbols = [s.rstrip("_") for s in symbols]
    else:
        symbols = [s.upper() for s in args.symbols]
    if not symbols:
        ap.print_help(); return 2

    print(f"Auditing {len(symbols)} symbols vs Yahoo Finance "
          f"(tolerance ±{args.tolerance} MB)...", file=sys.stderr)

    reports: List[dict] = []
    counters = {"ok": 0, "mismatch": 0, "no_overlap": 0, "error": 0}
    for i, sym in enumerate(symbols, 1):
        try:
            rep = compare_symbol(sym, tolerance=args.tolerance)
        except Exception as e:
            rep = {"symbol": sym, "status": "error", "error": str(e)[:200]}
        reports.append(rep)
        counters[rep["status"]] = counters.get(rep["status"], 0) + 1
        miss = len(rep.get("mismatches", []))
        tag = {"ok": "OK", "mismatch": "DIFF", "no_overlap": "NONE",
               "error": "ERR"}[rep["status"]]
        extra = f"  ({miss} mismatch)" if miss else ""
        print(f"  [{i:4d}/{len(symbols)}] {tag:5s} {sym:8s}{extra}",
              file=sys.stderr)
        time.sleep(args.sleep)

    summary = {
        "total": len(symbols),
        **counters,
        "tolerance": args.tolerance,
    }
    print(f"\nSummary: {summary}", file=sys.stderr)

    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": summary, "reports": reports},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
