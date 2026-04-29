"""scripts/verify_against_yahoo_fiscal.py — fiscal-year-aware Yahoo audit.

Same idea as verify_against_yahoo.py but maps each symbol's fiscal
quarter to the calendar quarter Yahoo reports under, using the
fiscal-end month from reference/fiscal_year.json.

For a symbol with fiscal_end_month M:
  Fiscal year Y starts at month (M+1) of calendar year Y-1 (when M<12)
  and runs until month M of year Y.

So for BTS (M=3, fiscal Y ends Mar Y):
  Fiscal Y, Q1 = Apr-Jun (Y-1) calendar → Yahoo end-of-quarter 6/30 (Y-1)
  Fiscal Y, Q2 = Jul-Sep (Y-1) calendar → Yahoo 9/30 (Y-1)
  Fiscal Y, Q3 = Oct-Dec (Y-1) calendar → Yahoo 12/31 (Y-1)
  Fiscal Y, Q4 = Jan-Mar Y calendar     → Yahoo 3/31 Y

For Dec-fiscal (M=12) the mapping is identity.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yfinance


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ingest.zip_downloader import safe_symbol_dir


PROCESSED_DIR = Path("data/processed")
FISCAL_PATH = Path("reference/fiscal_year.json")


def _gregorian_to_thai(g: int) -> int:
    return g + 543


def fiscal_qtr_to_calendar(
    fiscal_year_be: int, q_num: int, fiscal_end_month: int
) -> Tuple[int, int]:
    """Return (calendar_year_be, calendar_quarter_end_month) for the
    fiscal quarter labelled (fiscal_year_be, q_num).

    q_num: 1..4
    """
    # Calendar end-month of fiscal Q1 = (fiscal_end_month - 9) mod 12, then
    # add 3 months for each subsequent fiscal quarter.
    fiscal_end_year = fiscal_year_be
    # Fiscal Q4 ends at fiscal_end_month of fiscal_year_be.
    # Fiscal Q1 ends 9 months earlier.
    months_back = (4 - q_num) * 3   # Q4: 0, Q3: 3, Q2: 6, Q1: 9
    end_month = fiscal_end_month - months_back
    end_year_be = fiscal_end_year
    while end_month <= 0:
        end_month += 12
        end_year_be -= 1
    return end_year_be, end_month


def fetch_yahoo_by_calendar_qtr(symbol: str) -> Dict[Tuple[int, int], float]:
    """Return {(thai_year, end_month): net_income_MB} from Yahoo
    quarterly_financials. Each key is the calendar-quarter end."""
    out: Dict[Tuple[int, int], float] = {}
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
            thai_y = _gregorian_to_thai(col.year)
            out[(thai_y, col.month)] = float(val) / 1e6
        except Exception:
            continue
    return out


def load_local_quarterly(symbol: str) -> Dict[int, Dict[str, Optional[float]]]:
    path = PROCESSED_DIR / safe_symbol_dir(symbol) / "financials.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    qh = raw.get("quarterly_history", {})
    return {int(y): qs for y, qs in qh.items()}


def compare_symbol(
    symbol: str, fiscal_end_month: int, tolerance: float = 0.5
) -> dict:
    yh = fetch_yahoo_by_calendar_qtr(symbol)
    local = load_local_quarterly(symbol)
    if not yh or not local:
        return {"symbol": symbol, "fiscal_end_month": fiscal_end_month,
                "status": "no_overlap"}

    mismatches: List[dict] = []
    matched = 0
    total = 0
    for fy, qs in local.items():
        for q in ("Q1", "Q2", "Q3", "Q4"):
            local_v = qs.get(q)
            if local_v is None:
                continue
            cal_y, cal_m = fiscal_qtr_to_calendar(fy, int(q[1]),
                                                    fiscal_end_month)
            yh_v = yh.get((cal_y, cal_m))
            if yh_v is None:
                continue
            total += 1
            if abs(yh_v - local_v) <= max(tolerance, abs(yh_v) * 0.001):
                matched += 1
            else:
                mismatches.append({
                    "fiscal_year": fy, "fiscal_q": q,
                    "calendar_year": cal_y, "calendar_month": cal_m,
                    "yahoo": round(yh_v, 4),
                    "local": round(local_v, 4),
                    "delta": round(local_v - yh_v, 4),
                })
    return {
        "symbol": symbol,
        "fiscal_end_month": fiscal_end_month,
        "status": "ok" if not mismatches else "mismatch",
        "matched": matched, "total": total,
        "mismatches": mismatches,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tolerance", type=float, default=0.5)
    ap.add_argument("--out", default=None)
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    fiscal_map: dict[str, int] = {}
    if FISCAL_PATH.exists():
        fiscal_map = json.loads(FISCAL_PATH.read_text(encoding="utf-8"))

    if args.all:
        symbols = sorted(p.name.rstrip("_") for p in PROCESSED_DIR.iterdir()
                         if p.is_dir() and (p / "financials.json").exists())
    else:
        symbols = [s.upper() for s in args.symbols]
    if not symbols:
        ap.print_help(); return 2

    print(f"Auditing {len(symbols)} symbols against Yahoo "
          f"(fiscal-aware, tolerance ±{args.tolerance} MB)...",
          file=sys.stderr)

    reports: List[dict] = []
    counters = {"ok": 0, "mismatch": 0, "no_overlap": 0, "error": 0}
    for i, sym in enumerate(symbols, 1):
        end_m = fiscal_map.get(sym, 12)
        try:
            rep = compare_symbol(sym, end_m, tolerance=args.tolerance)
        except Exception as e:
            rep = {"symbol": sym, "status": "error", "error": str(e)[:200]}
        reports.append(rep)
        counters[rep["status"]] = counters.get(rep["status"], 0) + 1
        miss = len(rep.get("mismatches", []))
        tag = {"ok": "OK", "mismatch": "DIFF", "no_overlap": "NONE",
               "error": "ERR"}[rep["status"]]
        extra = f"  ({miss} mismatch, M={end_m})" if miss else ""
        if miss or rep["status"] != "ok":
            print(f"  [{i:4d}/{len(symbols)}] {tag:5s} {sym:8s}{extra}",
                  file=sys.stderr)
        time.sleep(args.sleep)

    summary = {"total": len(symbols), **counters,
               "tolerance": args.tolerance}
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
