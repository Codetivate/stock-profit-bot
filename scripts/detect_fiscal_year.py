"""scripts/detect_fiscal_year.py — figure out each symbol's fiscal-year-end
month from the SET API's company-highlight Q9 endDate.

SET reports the fiscal-year boundary in the `endDate` field
(e.g. BTS Q9 endDate = "2022-03-31" means fiscal year ends March).
Yahoo Finance reports by calendar quarter, so we need this mapping
to remap our local fiscal-year-keyed data into calendar-year-keyed
data for the Yahoo cross-check.

Output: reference/fiscal_year.json — {symbol: end_month}.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ingest.browser import SetSession


PROCESSED_DIR = Path("data/processed")
OUT = Path("reference/fiscal_year.json")


def detect_one(session: SetSession, symbol: str) -> int | None:
    """Return the fiscal-year-end month (1-12) or None if API fails."""
    url = (f"https://www.set.or.th/api/set/stock/{symbol}/"
           f"company-highlight/financial-data?lang=th")
    referer = (f"https://www.set.or.th/th/market/product/stock/quote/"
               f"{symbol}/financial-statement/company-highlights")
    try:
        rows = session.request_json(url, referer=referer)
    except Exception:
        return None
    for r in rows:
        if r.get("quarter") != "Q9":
            continue
        end = r.get("endDate", "")
        if not end:
            continue
        # endDate is ISO-ish "2022-03-31T00:00:00+07:00"
        try:
            month = int(end[5:7])
            return month
        except ValueError:
            continue
    return None


def main() -> int:
    syms = sorted(p.name.rstrip("_") for p in PROCESSED_DIR.iterdir()
                  if p.is_dir() and (p / "financials.json").exists())
    print(f"detecting fiscal end for {len(syms)} symbols...", file=sys.stderr)
    out: dict[str, int] = {}
    if OUT.exists():
        out = json.loads(OUT.read_text(encoding="utf-8"))
    with SetSession() as s:
        for i, sym in enumerate(syms, 1):
            if sym in out:
                continue
            m = detect_one(s, sym)
            if m is not None:
                out[sym] = m
                if m != 12:
                    print(f"  [{i:4d}/{len(syms)}] {sym:8s} → "
                          f"month {m:02d}", file=sys.stderr)
            time.sleep(0.1)
            # Periodically flush to disk in case of interruption
            if i % 50 == 0:
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(
                    json.dumps(out, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    # Summary by month
    from collections import Counter
    cnt = Counter(out.values())
    print(f"\nFiscal-year-end distribution:", file=sys.stderr)
    for month in sorted(cnt):
        print(f"  month {month:02d}: {cnt[month]} symbols", file=sys.stderr)
    print(f"wrote {OUT} ({len(out)} symbols)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
