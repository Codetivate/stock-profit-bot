"""batch_send_telegram.py — push every symbol's chart to Telegram in order.

For each symbol with parsed financials, this script:
  1. Loads our `data/processed/{SYMBOL}/financials.json`.
  2. Cross-checks the annual FullYear values against SET's
     company-highlight API (the same JSON the SET website uses).
  3. Generates the standard chart PNG.
  4. Sends it to Telegram with a caption prefixed `[N/total] SYMBOL`
     and either a ✅ "matches SET" stamp or a ❌ summary of the
     mismatched years — so a human reviewer can scroll the channel
     and spot symbols that need parser attention.

Designed for the 932-symbol audit pass: gives the reviewer a continuous
ordered sequence of charts plus a clear pass/fail signal per symbol.

Usage:
    python scripts/batch_send_telegram.py                  # all symbols
    python scripts/batch_send_telegram.py --start 250      # resume from 250
    python scripts/batch_send_telegram.py --limit 10       # only 10
    python scripts/batch_send_telegram.py --only-diffs     # send only mismatched
    python scripts/batch_send_telegram.py --dry-run        # don't actually send
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Add repo root for `src` imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from command_handler import (  # noqa: E402
    build_rich_caption,
    find_latest_quarter,
    get_company_name,
    load_symbol_history,
)
from make_chart_html import make_chart  # noqa: E402
from src.ingest.browser import SetSession  # noqa: E402
from telegram_client import TelegramClient  # noqa: E402


PROCESSED_DIR = Path("data/processed")
HIGHLIGHT_API = (
    "https://www.set.or.th/api/set/stock/{symbol}/"
    "company-highlight/financial-data?lang=th"
)
HIGHLIGHT_REFERER = (
    "https://www.set.or.th/th/market/product/stock/quote/{symbol}/"
    "financial-statement/company-highlights"
)


def fetch_set_annual(session: SetSession, symbol: str) -> dict[int, float]:
    """Return {thai_year: net_profit_MB} from SET's highlight API.
    SET reports annual rows as quarter == "Q9" in thousands of baht."""
    url = HIGHLIGHT_API.format(symbol=symbol)
    referer = HIGHLIGHT_REFERER.format(symbol=symbol)
    rows = session.request_json(url, referer=referer)
    out: dict[int, float] = {}
    for r in rows:
        if r.get("quarter") != "Q9":
            continue
        np_raw = r.get("netProfit")
        if np_raw is None:
            continue
        # SET uses Gregorian years; processed data is keyed by Thai year.
        out[int(r["year"]) + 543] = float(np_raw) / 1000.0
    return out


def compare_to_set(
    history: dict, set_annual: dict[int, float], tolerance: float
) -> tuple[bool, list[dict]]:
    """Return (all_match, list_of_mismatches). Mismatch dict has
    ``year``, ``set``, ``local``, ``diff`` (all rounded to 2dp)."""
    mismatches = []
    matched = 0
    for y, set_val in sorted(set_annual.items()):
        local_val = history[y].sum() if y in history else None
        if local_val is None:
            continue
        diff = local_val - set_val
        if abs(diff) > tolerance:
            mismatches.append({
                "year": y,
                "set": round(set_val, 2),
                "local": round(local_val, 2),
                "diff": round(diff, 2),
            })
        else:
            matched += 1
    all_match = matched > 0 and not mismatches
    return all_match, mismatches


def collect_symbols() -> list[str]:
    return sorted(p.name for p in PROCESSED_DIR.iterdir()
                  if p.is_dir() and (p / "financials.json").exists())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1,
                    help="1-based index to start from (resume mid-batch)")
    ap.add_argument("--limit", type=int, default=0,
                    help="max symbols to send this run (0 = no limit)")
    ap.add_argument("--only-diffs", action="store_true",
                    help="send only symbols whose annual doesn't match SET")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="max abs(diff) MB treated as a SET match (default 0.05)")
    ap.add_argument("--sleep", type=float, default=2.0,
                    help="seconds between sends (Telegram rate-limit cushion)")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and verify, but don't send to Telegram")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not args.dry_run and (not token or not chat_id):
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars required",
              file=sys.stderr)
        return 2

    symbols = collect_symbols()
    total = len(symbols)
    print(f"Collected {total} symbols. Starting at index {args.start}.")

    tg = TelegramClient(token) if not args.dry_run else None

    # Phase 1: pre-fetch SET annual snapshots for every symbol we plan to
    # touch. SetSession holds a sync Playwright browser, and make_chart's
    # HTML→PNG renderer also uses sync Playwright — two sync_playwright
    # contexts can't coexist in the same thread, so we collect SET data
    # first, close the SET browser, then render charts.
    in_scope = []
    for i, symbol in enumerate(symbols, 1):
        if i < args.start:
            continue
        if args.limit and len(in_scope) >= args.limit:
            break
        in_scope.append((i, symbol))

    print(f"Phase 1/2: fetching SET snapshots for {len(in_scope)} symbols…")
    set_data: dict[str, tuple[dict[int, float], Optional[str]]] = {}
    with SetSession(warm_symbol=in_scope[0][1]) as session:
        for n, (i, symbol) in enumerate(in_scope, 1):
            try:
                snap = fetch_set_annual(session, symbol)
                set_data[symbol] = (snap, None)
            except Exception as e:
                set_data[symbol] = ({}, str(e)[:120])
            if n % 25 == 0:
                print(f"      fetched {n}/{len(in_scope)}")

    print(f"Phase 2/2: rendering + sending charts…")
    sent = 0
    skipped = 0
    failed = 0
    for i, symbol in in_scope:
            position = f"[{i}/{total}]"

            history = load_symbol_history(symbol)
            if not history:
                print(f"  {position} {symbol}: NO_DATA — skipped")
                skipped += 1
                continue

            latest_year, latest_q = find_latest_quarter(history)
            if not latest_year or not latest_q:
                print(f"  {position} {symbol}: NO_QUARTER — skipped")
                skipped += 1
                continue

            set_annual, set_err = set_data.get(symbol, ({}, "missing"))

            if set_annual:
                ok, mismatches = compare_to_set(history, set_annual,
                                                tolerance=args.tolerance)
            else:
                ok, mismatches = False, []

            if args.only_diffs and ok:
                print(f"  {position} {symbol}: ✅ — skipped (only-diffs mode)")
                skipped += 1
                continue

            # Resolve company name + report date
            raw = json.loads(
                (PROCESSED_DIR / symbol / "financials.json")
                .read_text(encoding="utf-8")
            )
            company = (
                raw.get("company_name_en")
                or raw.get("company_name_th")
                or get_company_name(symbol)
                or symbol
            )
            updated_at = raw.get("updated_at", "")

            # Build status line
            if set_err:
                status_line = f"⚠️ SET API: {set_err}"
            elif not set_annual:
                status_line = "⚠️ SET ไม่มีข้อมูล (อาจเป็น fund/REIT)"
            elif ok:
                status_line = (
                    f"✅ ตรง SET — annual {len(set_annual)} ปี"
                )
            else:
                bits = "  ".join(
                    f"{m['year']}: เรา={m['local']:+.2f} SET={m['set']:+.2f}"
                    for m in mismatches[:3]
                )
                more = "" if len(mismatches) <= 3 else f"  (+{len(mismatches)-3})"
                status_line = f"❌ ไม่ตรง SET: {bits}{more}"

            # Generate chart and caption
            try:
                png = make_chart(
                    symbol=symbol,
                    company_name=company,
                    history=history,
                    latest_year=latest_year,
                    latest_quarter=latest_q,
                    report_date=updated_at,
                    period_label=f"{latest_q}/{latest_year}",
                )
            except Exception as e:
                print(f"  {position} {symbol}: chart_error: {e}")
                failed += 1
                continue

            base_caption = build_rich_caption(
                symbol=symbol,
                history=history,
                latest_year=latest_year,
                latest_quarter=latest_q,
                report_date=updated_at,
                header_prefix=f"<b>{position}  ·  ตรวจสอบ {symbol}</b>",
            )
            caption = f"{base_caption}\n\n<i>{status_line}</i>"

            if args.dry_run:
                tag = "OK " if ok else ("ERR" if set_err else "DIFF")
                print(f"  {position} {symbol}: {tag}  {status_line}")
                sent += 1
                continue

            try:
                tg.send_photo(chat_id, png, caption=caption,
                              filename=f"{symbol}.png")
                tag = "OK " if ok else "DIFF"
                print(f"  {position} {symbol}: {tag} sent")
                sent += 1
            except Exception as e:
                print(f"  {position} {symbol}: send_error: {e}")
                failed += 1

            time.sleep(args.sleep)

    print(f"\nDone. sent={sent}  skipped={skipped}  failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
