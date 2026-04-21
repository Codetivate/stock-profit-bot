"""
parse_all.py — Parse all downloaded zips for a symbol and build history

Usage:
    python parse_all.py CPALL

Takes all zip files in ./downloads/{SYMBOL}/
Parses them and computes standalone Q1-Q4 values
(SET reports are accumulated, so we subtract).

Output: data/{SYMBOL}.json with full history.
"""
import os
import re
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional

from parsers.parse_set_zip import parse_zip, FinancialData


def detect_period_from_filename(filename: str) -> Dict:
    """Detect period info from SET filename.

    Format: {code}FIN{DD}{MM}{YYYY}{HHMMSS}{seq}T.zip
    The filing date tells us approximately which period:
      - Filed Feb-Apr  → Annual (Q4)
      - Filed May-Jun  → Q1
      - Filed Jul-Sep  → Q2 / Half
      - Filed Oct-Nov  → Q3 / 9-month
    """
    m = re.match(r"(\d{4})(FIN)(\d{2})(\d{2})(\d{4})", filename, re.IGNORECASE)
    if not m:
        return {}
    code, _, dd, mm, yyyy = m.groups()
    month = int(mm)
    year_greg = int(yyyy)
    year_thai = year_greg + 543 - 543  # Thai buddhist year

    # Heuristic: filing month → period type
    if month in (2, 3, 4):
        ptype = "annual"  # Annual reports filed Q1 of next year
        ref_year = year_thai - 1  # Reports FY of previous year
    elif month in (5, 6):
        ptype = "q1"
        ref_year = year_thai
    elif month in (7, 8, 9):
        ptype = "half"
        ref_year = year_thai
    elif month in (10, 11):
        ptype = "9month"
        ref_year = year_thai
    else:
        ptype = "unknown"
        ref_year = year_thai

    return {
        "filing_date": f"{yyyy}-{mm}-{dd}",
        "filing_month": month,
        "period_type_guess": ptype,
        "reference_year_thai": ref_year,
    }


def parse_all_zips(symbol: str, download_dir: str = "./downloads") -> List[dict]:
    """Parse all zip files for a symbol, return list of reports."""
    symbol_dir = Path(download_dir) / symbol.upper()
    if not symbol_dir.exists():
        print(f"❌ Directory not found: {symbol_dir}")
        return []

    reports = []
    zips = sorted(symbol_dir.glob("*.zip")) + sorted(symbol_dir.glob("*.ZIP"))
    print(f"\nFound {len(zips)} zip files in {symbol_dir}")

    for zip_path in zips:
        filename = zip_path.name
        print(f"\n→ {filename}")

        # Parse filename for period hint
        meta = detect_period_from_filename(filename)
        print(f"  Filed: {meta.get('filing_date')}  ·  "
              f"Guess: {meta.get('period_type_guess')}  ·  "
              f"Ref year: {meta.get('reference_year_thai')}")

        # Parse Excel
        try:
            data = parse_zip(str(zip_path), symbol=symbol.upper())
            if not data:
                print("  ✗ Parse failed")
                continue

            print(f"  ✓ {data.period_label}  ·  "
                  f"shareholder_profit: {data.shareholder_profit:,.2f} MB"
                  if data.shareholder_profit else "  ✗ No profit data")

            report = {
                "filename": filename,
                "filing_date": meta.get("filing_date"),
                "period_type_guess": meta.get("period_type_guess"),
                "period_label": data.period_label,
                "detected_period_type": data.period_type,
                "year": data.year,
                "revenue": data.revenue,
                "net_profit": data.net_profit,
                "shareholder_profit": data.shareholder_profit,
                "eps": data.eps,
            }
            reports.append(report)
        except Exception as e:
            print(f"  ✗ Error: {e}")

    return reports


def compute_quarterly(reports: List[dict]) -> Dict[int, Dict[str, float]]:
    """Compute standalone Q1, Q2, Q3, Q4 values by subtraction.

    Input: list of reports, each is annual/q1/half/9month.
    Output: {year: {'Q1': val, 'Q2': val, 'Q3': val, 'Q4': val}}
    """
    # Group reports by year and period
    # Use period_type_guess (from filename) as it's more reliable
    by_year = {}  # {year: {ptype: value}}
    for r in reports:
        y = r["year"]
        ptype = r["period_type_guess"]
        sp = r["shareholder_profit"]
        if not y or sp is None:
            continue

        if y not in by_year:
            by_year[y] = {}
        by_year[y][ptype] = sp

    # Calculate Q1-Q4 for each year
    quarterly = {}
    for y, periods in by_year.items():
        quarterly[y] = {"Q1": None, "Q2": None, "Q3": None, "Q4": None}

        q1_val = periods.get("q1")
        half_val = periods.get("half")
        ninemon_val = periods.get("9month")
        annual_val = periods.get("annual")

        if q1_val is not None:
            quarterly[y]["Q1"] = q1_val

        if half_val is not None and q1_val is not None:
            quarterly[y]["Q2"] = half_val - q1_val

        if ninemon_val is not None and half_val is not None:
            quarterly[y]["Q3"] = ninemon_val - half_val

        if annual_val is not None and ninemon_val is not None:
            quarterly[y]["Q4"] = annual_val - ninemon_val

    return quarterly


def main():
    ap = argparse.ArgumentParser(description="Parse all SET zips for a symbol")
    ap.add_argument("symbol", help="Stock symbol, e.g. CPALL")
    ap.add_argument("--download-dir", default="./downloads")
    ap.add_argument("--data-dir", default="./data")
    args = ap.parse_args()

    symbol = args.symbol.upper()

    print(f"\n{'='*60}")
    print(f"Processing {symbol}")
    print(f"{'='*60}")

    reports = parse_all_zips(symbol, args.download_dir)
    if not reports:
        print("\n✗ No reports parsed")
        sys.exit(1)

    quarterly = compute_quarterly(reports)

    print(f"\n{'='*60}")
    print(f"QUARTERLY HISTORY (standalone, shareholder profit, MB)")
    print(f"{'='*60}")
    print(f"{'Year':<8} {'Q1':>12} {'Q2':>12} {'Q3':>12} {'Q4':>12} {'Sum':>12}")
    print("-" * 70)
    for y in sorted(quarterly.keys()):
        qs = quarterly[y]
        row = f"{y:<8}"
        total = 0
        has_all = True
        for q in ["Q1", "Q2", "Q3", "Q4"]:
            val = qs.get(q)
            if val is not None:
                row += f" {val:>12,.2f}"
                total += val
            else:
                row += f" {'—':>12}"
                has_all = False
        row += f" {total:>12,.2f}" if has_all else f" {'—':>12}"
        print(row)

    # Save to JSON
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = data_dir / f"{symbol}.json"

    output = {
        "symbol": symbol,
        "updated_at": reports[-1]["filing_date"] if reports else None,
        "raw_reports": reports,
        "quarterly_history": {str(y): q for y, q in quarterly.items()},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Saved to {out_path}")


if __name__ == "__main__":
    main()
