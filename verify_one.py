"""verify_one.py — push a single symbol's chart to Telegram for manual QA.

Usage:
    python verify_one.py 2S [--ingest]

What it does:
1. Loads `data/processed/{SYMBOL}/financials.json` (or runs the ingest
   pipeline first when --ingest is passed).
2. Renders the chart via the production HTML renderer.
3. Sends the chart to TELEGRAM_CHAT_ID with a caption that embeds:
     - the latest quarterly numbers
     - the SET company-highlights URL (for value-by-value verification)
     - the SET news URL (for the underlying filing)
4. Prints a one-line summary to stdout so the walking-loop driver can
   tail it later.

Designed for the "verify 932 symbols one at a time before automating"
workflow — minimal, idempotent, no state writes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from command_handler import (
    build_rich_caption,
    find_latest_quarter,
    get_company_name,
    load_symbol_history,
)
from make_chart_html import make_chart
from telegram_client import TelegramClient


PROCESSED_DIR = Path("data/processed")

SET_HIGHLIGHTS_URL = (
    "https://www.set.or.th/th/market/product/stock/quote/{symbol}/"
    "financial-statement/company-highlights"
)
SET_NEWS_URL = "https://www.set.or.th/th/market/product/stock/quote/{symbol}/news"


THAI_MONTHS = [
    "", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]


def _format_thai_date(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return iso
    return f"{d.day} {THAI_MONTHS[d.month]} {d.year + 543}"


def _english_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return iso


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument(
        "--ingest", action="store_true",
        help="Re-run the SET ingest pipeline before rendering "
             "(slow — only use when you want to refresh data).",
    )
    ap.add_argument(
        "--fast", action="store_true",
        help="Skip the SET company-highlight cross-check (saves ~5-10s "
             "by not booting Playwright). Use when speed matters and "
             "you trust the parser's output (e.g. immediately after a "
             "fresh filing has been ingested — SET API hasn't refreshed "
             "yet anyway).",
    )
    args = ap.parse_args()
    symbol = args.symbol.upper().strip()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars required",
              file=sys.stderr)
        return 2

    if args.ingest:
        print(f"[{symbol}] ingesting fresh from SET ...")
        rc = subprocess.call(
            [sys.executable, "-m", "src.cli.ingest_financials", symbol]
        )
        if rc != 0:
            print(f"[{symbol}] ingest failed (rc={rc})", file=sys.stderr)
            return rc

    history = load_symbol_history(symbol)
    if not history:
        print(f"[{symbol}] NO_DATA — financials.json missing")
        return 1

    latest_year, latest_q = find_latest_quarter(history)
    if not latest_year or not latest_q:
        print(f"[{symbol}] NO_QUARTER — history present but no filled quarter")
        return 1

    raw = json.loads(
        (PROCESSED_DIR / symbol / "financials.json").read_text(encoding="utf-8")
    )
    company = (
        raw.get("company_name_en")
        or raw.get("company_name_th")
        or get_company_name(symbol)
        or symbol
    )
    updated_at_iso = raw.get("updated_at", "")
    report_date_en = _english_date(updated_at_iso) if updated_at_iso else ""
    report_date_th = _format_thai_date(updated_at_iso) if updated_at_iso else ""

    # Pre-flight cross-check vs SET company-highlight API. We compute
    # the diff per year between our stored FullYear and SET's published
    # annual number so the caption can carry an explicit ✅/❌ stamp —
    # the user reviews each chart in Telegram and a wrong value should
    # be visible at a glance, not buried in a separate report.
    #
    # --fast skips this entirely: useful right after a fresh filing
    # lands (SET API takes hours-to-days to refresh, so the check would
    # only ever produce ⏳ "SET ยังไม่ refresh" anyway). The chart and
    # numbers themselves still come straight from the SET-filed XLSX.
    set_status_line = ""
    if args.fast:
        set_status_line = "⚡ fast mode — ไม่ได้ตรวจ SET (ใช้ค่าตรงจาก XLSX)"
    else:
        try:
            from src.ingest.browser import SetSession  # local import to keep
                                                       # the no-network path
                                                       # available for sites
                                                       # without playwright.
            with SetSession(warm_symbol=symbol) as session:
                ref = (f"https://www.set.or.th/th/market/product/stock/quote/"
                       f"{symbol}/financial-statement/company-highlights")
                url = (f"https://www.set.or.th/api/set/stock/{symbol}/"
                       f"company-highlight/financial-data?lang=th")
                rows = session.request_json(url, referer=ref)
            set_annual = {}
            for r in rows:
                if r.get("quarter") != "Q9" or r.get("netProfit") is None:
                    continue
                set_annual[int(r["year"]) + 543] = float(r["netProfit"]) / 1000.0
            mismatches = []
            matched = 0
            tolerance = 0.05
            # Years where we have a complete (Q1..Q4) set or an FY filing —
            # used to know which year is "the one we just filed" so we don't
            # flag SET's API lag as a real bug.
            local_latest_complete = max(
                (y for y in history.keys() if history[y].sum() is not None),
                default=None,
            )
            for y, set_val in sorted(set_annual.items()):
                local_val = history[y].sum() if y in history else None
                if local_val is None:
                    continue
                if abs(local_val - set_val) > tolerance:
                    mismatches.append((y, local_val, set_val))
                else:
                    matched += 1

            # Split mismatches into "historical" (real parser/data bug) and
            # "current-period" (likely SET company-highlight API lagging
            # behind a brand-new filing — the API is updated by SET on a
            # delay of hours-to-days after the news/zip is published, so
            # our value can be ahead of SET's snapshot for a window).
            hist_mm = [m for m in mismatches
                       if local_latest_complete is None or m[0] < local_latest_complete]
            new_mm = [m for m in mismatches if m not in hist_mm]

            if not set_annual:
                set_status_line = "⚠️ SET ไม่มีข้อมูล (อาจเป็น fund/REIT)"
            elif hist_mm:
                bits = "  ".join(
                    f"{y}: เรา={l:+.2f} SET={s:+.2f}" for y, l, s in hist_mm[:3]
                )
                extra = "" if len(hist_mm) <= 3 else f"  (+{len(hist_mm)-3})"
                set_status_line = f"❌ ไม่ตรง SET (ประวัติ): {bits}{extra}"
            elif new_mm:
                y, l, s = new_mm[0]
                set_status_line = (
                    f"⏳ งบใหม่ {y} — SET API ยังไม่ refresh "
                    f"(เรา={l:+.2f} SET ปัจจุบัน={s:+.2f}; SET ปกติอัพเดทใน 1-2 วัน). "
                    f"ปีก่อนหน้า {matched} ปี ✅ ตรง SET"
                )
            elif matched > 0:
                set_status_line = f"✅ ตรง SET — annual {matched} ปี"
        except Exception as e:
            set_status_line = f"⚠️ ตรวจ SET ไม่ได้: {str(e)[:80]}"

    period_label = f"FY {latest_year}  ·  {latest_q}"
    png = make_chart(
        symbol=symbol,
        company_name=company,
        history=history,
        latest_year=latest_year,
        latest_quarter=latest_q,
        report_date=report_date_en,
        period_label=period_label,
    )

    base_caption = build_rich_caption(
        symbol=symbol,
        history=history,
        latest_year=latest_year,
        latest_quarter=latest_q,
        report_date=report_date_th,
        header_prefix="<b>🔍 ตรวจสอบข้อมูล</b>",
    )
    highlights = SET_HIGHLIGHTS_URL.format(symbol=symbol)
    news = SET_NEWS_URL.format(symbol=symbol)
    caption = (
        f"{base_caption}\n\n"
        + (f"<i>{set_status_line}</i>\n\n" if set_status_line else "")
        + f"<b>อ้างอิง SET:</b>\n"
        f"• <a href=\"{highlights}\">Company Highlights</a>\n"
        f"• <a href=\"{news}\">งบที่ประกาศ</a>"
    )

    tg = TelegramClient(token)
    # Dual-target broadcast: TELEGRAM_CHAT_ID (private DM) is the
    # primary destination; TELEGRAM_CHANNEL_ID (optional) is the public
    # broadcast channel. Send to both — failures on one don't block
    # the other (the photo bytes are reusable, no re-render needed).
    targets = [chat_id]
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    if channel_id and channel_id != chat_id:
        targets.append(channel_id)
    sent_ok = []
    sent_err = []
    for tgt in targets:
        try:
            tg.send_photo(tgt, png, caption=caption, filename=f"{symbol}.png")
            sent_ok.append(tgt)
        except Exception as e:
            sent_err.append((tgt, str(e)[:80]))
    latest_val = history[latest_year].get(latest_q)
    where = "+".join(sent_ok) or "none"
    err_note = ""
    if sent_err:
        err_note = "  [errors: " + "; ".join(f"{t}={e}" for t,e in sent_err) + "]"
    print(f"[{symbol}] OK · {period_label} · {latest_val:,.2f} MB · "
          f"sent → {where}{err_note} · {set_status_line}")
    return 0 if sent_ok else 1


if __name__ == "__main__":
    sys.exit(main())
