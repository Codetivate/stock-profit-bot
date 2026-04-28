"""verify_against_set.py — sweep parsed financials vs SET's published numbers.

For each requested symbol:
  1. Fetch SET's company-highlight financial-data API (the same JSON the
     SET website renders on the "Company Highlights" page).
  2. Load our parsed `data/processed/{SYMBOL}/financials.json`.
  3. Compare annual net-profit values year-by-year and flag any
     mismatch beyond the rounding tolerance (default ±0.01 MB).

The SET highlight API only exposes annual ("Q9") figures, so this
script is the "gross check" — if our annual matches, the parser at
least got the parent-share total right. Quarterly mismatches need the
quarterly view, which is on the SET financial-statement page (rendered
in the browser, no flat JSON endpoint we've found yet).

Usage:
    python scripts/verify_against_set.py 2S
    python scripts/verify_against_set.py 2S CPALL PTT
    python scripts/verify_against_set.py --all          # every symbol under data/processed/
    python scripts/verify_against_set.py --all --tolerance 0.05
    python scripts/verify_against_set.py --all --out data/validation/set_audit.json

Exit code is non-zero when any mismatch is found, so this is safe to
wire into CI as a regression gate after a parser change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Make `python scripts/verify_against_set.py` work without needing a
# wrapper or PYTHONPATH=. — add the repo root (parent of scripts/) to
# sys.path before importing the in-repo src package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingest.browser import SetSession  # noqa: E402


PROCESSED_DIR = Path("data/processed")

API_URL = (
    "https://www.set.or.th/api/set/stock/{symbol}/"
    "company-highlight/financial-data?lang=th"
)
REFERER = (
    "https://www.set.or.th/th/market/product/stock/quote/{symbol}/"
    "financial-statement/company-highlights"
)


def _gregorian_to_thai(gregorian: int) -> int:
    """SET's API returns Gregorian years; our processed data is keyed by
    the Thai Buddhist year. Two centuries' worth of offset, stable since
    1941."""
    return gregorian + 543


def fetch_set_annual(session: SetSession, symbol: str) -> dict[int, float]:
    """Return {thai_year: net_profit_MB} from SET's highlight API.

    Only the annual rows (``quarter == "Q9"``) are returned — SET tags
    quarterly rows separately (Q1/Q2/Q3) but the annual page only
    surfaces Q9. Values arrive in thousands of baht, returned in millions.
    """
    url = API_URL.format(symbol=symbol)
    referer = REFERER.format(symbol=symbol)
    rows = session.request_json(url, referer=referer)
    out: dict[int, float] = {}
    for r in rows:
        if r.get("quarter") != "Q9":
            continue
        np_raw = r.get("netProfit")
        if np_raw is None:
            continue
        # SET reports in พันบาท (thousand baht); divide by 1000 → MB.
        out[_gregorian_to_thai(int(r["year"]))] = float(np_raw) / 1000.0
    return out


_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_dir(sym: str) -> str:
    return f"{sym}_" if sym.upper() in _WIN_RESERVED else sym


def load_local_annuals(symbol: str) -> dict[int, Optional[float]]:
    """Read our parsed FullYear values from financials.json."""
    path = PROCESSED_DIR / _safe_dir(symbol) / "financials.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    qh = raw.get("quarterly_history", {})
    out: dict[int, Optional[float]] = {}
    for y_str, qs in qh.items():
        out[int(y_str)] = qs.get("FullYear")
    return out


def compare_symbol(
    session: SetSession, symbol: str, *, tolerance: float
) -> dict:
    """Run the comparison for one symbol and return a report dict."""
    try:
        set_annual = fetch_set_annual(session, symbol)
    except Exception as e:
        return {
            "symbol": symbol,
            "status": "fetch_failed",
            "error": str(e)[:200],
            "mismatches": [],
        }

    local_annual = load_local_annuals(symbol)
    if not local_annual:
        return {
            "symbol": symbol,
            "status": "no_local_data",
            "mismatches": [],
        }

    mismatches = []
    matched = 0
    set_only = []
    local_only = []

    for y, set_val in sorted(set_annual.items()):
        local_val = local_annual.get(y)
        if local_val is None:
            set_only.append(y)
            continue
        diff = local_val - set_val
        if abs(diff) > tolerance:
            mismatches.append({
                "year": y,
                "set": round(set_val, 3),
                "local": round(local_val, 3),
                "diff": round(diff, 3),
            })
        else:
            matched += 1

    for y in sorted(local_annual.keys()):
        if y not in set_annual:
            local_only.append(y)

    if mismatches:
        status = "mismatch"
    elif matched == 0:
        status = "no_overlap"
    else:
        status = "ok"

    return {
        "symbol": symbol,
        "status": status,
        "matched_years": matched,
        "mismatches": mismatches,
        "set_only_years": set_only,   # SET has it, we don't
        "local_only_years": local_only,  # we have it, SET doesn't
    }


def collect_symbols(args) -> list[str]:
    if args.symbols:
        return [s.upper() for s in args.symbols]
    if args.all:
        return sorted(p.name for p in PROCESSED_DIR.iterdir()
                      if p.is_dir() and (p / "financials.json").exists())
    raise SystemExit("specify symbols or --all")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", help="symbols to verify")
    ap.add_argument("--all", action="store_true",
                    help="verify every symbol under data/processed/")
    ap.add_argument("--tolerance", type=float, default=0.01,
                    help="max abs(diff) in MB to treat as a match (default 0.01)")
    ap.add_argument("--out", type=Path,
                    help="write full report JSON here (default: stdout summary only)")
    args = ap.parse_args()

    symbols = collect_symbols(args)
    print(f"Verifying {len(symbols)} symbol(s) against SET highlights "
          f"(tolerance ±{args.tolerance} MB)…\n")

    reports = []
    n_ok = n_mismatch = n_failed = n_no_overlap = 0
    with SetSession(warm_symbol=symbols[0]) as session:
        for i, sym in enumerate(symbols, 1):
            rep = compare_symbol(session, sym, tolerance=args.tolerance)
            reports.append(rep)
            st = rep["status"]
            tag = {"ok": "OK   ", "mismatch": "DIFF ",
                   "fetch_failed": "FAIL ", "no_overlap": "NONE ",
                   "no_local_data": "NONE "}[st]
            extra = ""
            if rep.get("mismatches"):
                extra = "  " + "  ".join(
                    f"{m['year']}: ours={m['local']:+.2f} set={m['set']:+.2f} d={m['diff']:+.2f}"
                    for m in rep["mismatches"]
                )
            print(f"  [{i:3}/{len(symbols)}] {tag} {sym:8s}{extra}")
            if st == "ok":
                n_ok += 1
            elif st == "mismatch":
                n_mismatch += 1
            elif st == "fetch_failed":
                n_failed += 1
            else:
                n_no_overlap += 1

    print(f"\nSummary:  ok={n_ok}  mismatch={n_mismatch}  "
          f"failed={n_failed}  no_overlap={n_no_overlap}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"reports": reports, "tolerance": args.tolerance},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nFull report saved to {args.out}")

    return 0 if n_mismatch == 0 and n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
