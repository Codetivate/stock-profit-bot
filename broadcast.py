"""
broadcast.py — Broadcast new financial reports to Telegram channel

Runs on schedule (via GitHub Actions).
1. Loads whitelist of symbols to monitor
2. For each symbol, checks SET news page for new FIN reports
3. If a new report is found (not in state file):
   a. Downloads the zip
   b. Parses it
   c. Updates history
   d. Generates chart
   e. Sends to Telegram channel
   f. Records in state

Usage:
    python broadcast.py
"""
import os
import re
import sys
import json
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

from parsers.parse_set_zip import parse_zip, FinancialData
from parse_all import compute_quarterly, detect_period_from_filename
from make_chart_html import make_chart, QuarterlyData
from telegram_client import TelegramClient, format_caption


# ═══ Config ═══
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
WHITELIST_FILE = Path("whitelist.json")
STATE_FILE = Path("data/broadcast_state.json")
DATA_DIR = Path("data")

SET_NEWS_URL = "https://www.set.or.th/th/market/product/stock/quote/{symbol}/news"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def load_whitelist() -> List[str]:
    """Load symbol whitelist from JSON file."""
    if WHITELIST_FILE.exists():
        return json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
    return ["CPALL"]  # default


def load_state() -> Dict:
    """Load broadcast state — which zip filenames have been processed."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"processed": []}


def save_state(state: Dict):
    """Save broadcast state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_symbol_history(symbol: str) -> Dict[int, QuarterlyData]:
    """Load accumulated quarterly history for a symbol."""
    path = DATA_DIR / f"{symbol}.json"
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    quarterly = raw.get("quarterly_history", {})

    history = {}
    for y_str, qs in quarterly.items():
        y = int(y_str)
        history[y] = QuarterlyData(
            year=y,
            q1=qs.get("Q1"),
            q2=qs.get("Q2"),
            q3=qs.get("Q3"),
            q4=qs.get("Q4"),
        )
    return history


def save_symbol_history(symbol: str, raw_data: dict):
    """Save updated history JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{symbol}.json"
    path.write_text(
        json.dumps(raw_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def fetch_news_page(symbol: str) -> str:
    """Fetch SET news HTML for a symbol."""
    url = SET_NEWS_URL.format(symbol=symbol)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def find_latest_fin_zip(symbol: str) -> Optional[Dict]:
    """Search SET news page for the most recent FIN zip.

    Returns dict with {url, filename} or None.

    NOTE: The SET news page is JavaScript-heavy. For production use,
    consider using Playwright (like tfex scraper) or the public
    data.set.or.th endpoints if available.
    """
    html = fetch_news_page(symbol)

    # Look for weblink.set.or.th zip URLs in the HTML
    # Pattern: https://weblink.set.or.th/dat/news/YYYYMM/{code}FIN...T.zip
    pattern = r"https://weblink\.set\.or\.th/dat/news/\d{6}/[\w]+FIN[\d]+T\.zip"
    matches = re.findall(pattern, html)

    if not matches:
        return None

    # Take the first (most recent) match
    url = matches[0]
    filename = os.path.basename(urlparse(url).path)
    return {"url": url, "filename": filename}


def download_zip(url: str, out_path: str) -> bool:
    """Download a zip file from SET."""
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)
    return True


def get_latest_report_info(history: Dict[int, QuarterlyData]) -> tuple:
    """Find the most recent (year, quarter) in history."""
    if not history:
        return None, None

    latest_year = max(history.keys())
    qdata = history[latest_year]
    # Find last filled quarter
    for q in ["Q4", "Q3", "Q2", "Q1"]:
        if qdata.get(q) is not None:
            return latest_year, q
    return latest_year, None


def process_symbol(symbol: str, tg: TelegramClient, state: Dict):
    """Process one symbol: check for new report, broadcast if found."""
    print(f"\n{'='*60}")
    print(f"Processing {symbol}")
    print(f"{'='*60}")

    # Step 1: Find latest zip on SET
    latest = find_latest_fin_zip(symbol)
    if not latest:
        print(f"  ⚠️  No FIN zip found on SET news page")
        return

    filename = latest["filename"]
    print(f"  Latest zip: {filename}")

    # Step 2: Check if already processed
    key = f"{symbol}:{filename}"
    if key in state.get("processed", []):
        print(f"  ✓ Already processed, skipping")
        return

    # Step 3: Download the zip
    print(f"  ↓ Downloading {filename}...")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / filename
        try:
            download_zip(latest["url"], str(zip_path))
        except Exception as e:
            print(f"  ✗ Download failed: {e}")
            return

        # Step 4: Parse
        data = parse_zip(str(zip_path), symbol=symbol)
        if not data or data.shareholder_profit is None:
            print(f"  ✗ Parse failed or no profit data")
            return

        print(f"  ✓ Parsed: {data.period_label}  ·  "
              f"{data.shareholder_profit:,.2f} MB")

        # Step 5: Update symbol history
        update_symbol_history(symbol, data, filename)

        # Step 6: Generate chart from updated history
        history = load_symbol_history(symbol)
        latest_year, latest_q = get_latest_report_info(history)
        if not latest_year or not latest_q:
            print(f"  ⚠️  Insufficient history to generate chart")
            return

        # Make chart
        print(f"  🎨 Generating chart...")
        png = make_chart(
            symbol=symbol,
            company_name=get_company_name(symbol),
            history=history,
            latest_year=latest_year,
            latest_quarter=latest_q,
            report_date=format_report_date(data.filename),
            period_label=f"{latest_q}/{latest_year}",
        )

        # Step 7: Send to Telegram
        caption = format_caption({
            "symbol": symbol,
            "period_label": data.period_label,
            "shareholder_profit": data.shareholder_profit,
            "shareholder_profit_prior": data.shareholder_profit_prior,
            "revenue": data.revenue,
            "revenue_prior": data.revenue_prior,
        })

        print(f"  📤 Posting to Telegram...")
        # Dual-target: DM + optional channel. Mark as processed only if
        # at least one target succeeded — that way a transient channel
        # outage doesn't make us re-broadcast on the next tick.
        targets = [TELEGRAM_CHAT_ID]
        if TELEGRAM_CHANNEL_ID and TELEGRAM_CHANNEL_ID != TELEGRAM_CHAT_ID:
            targets.append(TELEGRAM_CHANNEL_ID)
        any_ok = False
        for tgt in targets:
            try:
                tg.send_photo(
                    chat_id=tgt,
                    photo_bytes=png,
                    caption=caption,
                    filename=f"{symbol}_{latest_year}{latest_q}.png",
                )
                print(f"  ✓ Sent to {tgt}")
                any_ok = True
            except Exception as e:
                print(f"  ✗ Send to {tgt} failed: {e}")
        if any_ok:
            # Mark as processed
            state.setdefault("processed", []).append(key)
            save_state(state)


def update_symbol_history(symbol: str, data: FinancialData, filename: str):
    """Update the symbol's history JSON with new report data."""
    path = DATA_DIR / f"{symbol}.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = {
            "symbol": symbol,
            "raw_reports": [],
            "quarterly_history": {},
        }

    # Add raw report
    meta = detect_period_from_filename(filename)
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

    # Avoid duplicates
    existing_files = {r["filename"] for r in raw.get("raw_reports", [])}
    if filename not in existing_files:
        raw.setdefault("raw_reports", []).append(report)

    # Recompute quarterly from all raw reports
    quarterly = compute_quarterly(raw["raw_reports"])
    raw["quarterly_history"] = {str(y): q for y, q in quarterly.items()}
    raw["updated_at"] = meta.get("filing_date")

    save_symbol_history(symbol, raw)


def get_company_name(symbol: str) -> str:
    """Map symbol to company name."""
    names = {
        "CPALL": "CP All Public Company Limited",
        "PTT": "PTT Public Company Limited",
        "AOT": "Airports of Thailand",
        "SCB": "SCB X Public Company Limited",
        # Add more as whitelist grows
    }
    return names.get(symbol, symbol)


def format_report_date(filename: str) -> str:
    """Extract and format the report date from filename."""
    meta = detect_period_from_filename(filename)
    date_str = meta.get("filing_date", "")
    if not date_str:
        return ""
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except Exception:
        return date_str


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN env var not set")
        sys.exit(1)
    if not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID env var not set")
        sys.exit(1)

    whitelist = load_whitelist()
    state = load_state()

    print(f"Checking {len(whitelist)} symbols: {', '.join(whitelist)}")

    tg = TelegramClient(TELEGRAM_BOT_TOKEN)

    # Verify token
    try:
        me = tg.get_me()
        print(f"✓ Bot OK: @{me['result']['username']}")
    except Exception as e:
        print(f"❌ Bot token invalid: {e}")
        sys.exit(1)

    for symbol in whitelist:
        try:
            process_symbol(symbol, tg, state)
            time.sleep(2)  # Polite delay between SET requests
        except Exception as e:
            print(f"  ❌ Error processing {symbol}: {e}")
            continue

    print(f"\n{'='*60}")
    print(f"Done. {len(state.get('processed', []))} reports processed total.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
