"""
command_handler.py — Poll Telegram for /profit commands and reply

Handles DM commands like:
    /profit CPALL
    /profit PTT

Generates chart on-demand from stored history and sends back to user.

Usage:
    python command_handler.py
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime

from make_chart import make_chart, QuarterlyData
from telegram_client import TelegramClient


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
STATE_FILE = Path("data/command_state.json")
DATA_DIR = Path("data")


def load_state():
    """Load last_update_id so we don't reprocess messages."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_update_id": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_symbol_history(symbol: str):
    """Load quarterly history for a symbol, returns {year: QuarterlyData} or None."""
    path = DATA_DIR / f"{symbol}.json"
    if not path.exists():
        return None

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


def find_latest_quarter(history):
    """Return (year, quarter) of latest filled quarter."""
    if not history:
        return None, None
    latest_year = max(history.keys())
    qdata = history[latest_year]
    for q in ["Q4", "Q3", "Q2", "Q1"]:
        if qdata.get(q) is not None:
            return latest_year, q
    return latest_year, None


def handle_profit_command(tg: TelegramClient, chat_id, symbol: str):
    """Generate and send chart for a symbol."""
    symbol = symbol.upper().strip()

    history = load_symbol_history(symbol)
    if not history:
        tg.send_message(
            chat_id,
            f"❌ ยังไม่มีข้อมูลของ <b>{symbol}</b>\n\n"
            f"ระบบจะเก็บข้อมูลเมื่อมีงบใหม่ออก "
            f"หรือใช้คำสั่ง /profit กับหุ้นอื่นที่มีใน whitelist"
        )
        return

    latest_year, latest_q = find_latest_quarter(history)
    if not latest_year or not latest_q:
        tg.send_message(chat_id, f"❌ ข้อมูล {symbol} ยังไม่สมบูรณ์")
        return

    # Try to get period_label and company name from saved data
    path = DATA_DIR / f"{symbol}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))

    company_name = get_company_name(symbol)
    report_date = raw.get("updated_at", "")
    if report_date:
        try:
            dt = datetime.strptime(report_date, "%Y-%m-%d")
            report_date = dt.strftime("%d %b %Y")
        except Exception:
            pass

    # Generate chart
    png = make_chart(
        symbol=symbol,
        company_name=company_name,
        history=history,
        latest_year=latest_year,
        latest_quarter=latest_q,
        report_date=report_date,
        period_label=f"FY {latest_year}  ·  {latest_q}",
    )

    # Build simple caption
    latest_profit = history[latest_year].get(latest_q)
    caption = (
        f"● <b>{symbol}</b>  ·  FY{latest_year} {latest_q}\n"
        f"💰 กำไรสุทธิ: {latest_profit:,.2f} ล้านบาท"
    )

    tg.send_photo(
        chat_id=chat_id,
        photo_bytes=png,
        caption=caption,
        filename=f"{symbol}_{latest_year}{latest_q}.png",
    )


def get_company_name(symbol: str) -> str:
    names = {
        "CPALL": "CP All Public Company Limited",
        "PTT": "PTT Public Company Limited",
        "AOT": "Airports of Thailand",
        "SCB": "SCB X Public Company Limited",
    }
    return names.get(symbol, symbol)


def handle_help_command(tg: TelegramClient, chat_id):
    """Send help text."""
    msg = (
        "<b>📊 Stock Profit Bot</b>\n\n"
        "ใช้คำสั่งต่อไปนี้:\n\n"
        "<code>/profit SYMBOL</code>  — ดูกำไรหุ้น (เช่น /profit CPALL)\n"
        "<code>/help</code>  — แสดงคำสั่งที่ใช้ได้\n\n"
        "Bot จะ broadcast งบใหม่ใน channel อัตโนมัติเมื่อมีงบออก"
    )
    tg.send_message(chat_id, msg)


def process_update(update: dict, tg: TelegramClient):
    """Process a single Telegram update."""
    msg = update.get("message", {})
    if not msg:
        return

    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()

    if not text or not text.startswith("/"):
        return

    # Parse command
    parts = text.split(None, 1)  # split on any whitespace
    cmd = parts[0].lower()

    # Remove @botname suffix if present
    if "@" in cmd:
        cmd = cmd.split("@")[0]

    print(f"  → Command: {cmd}  arg: {parts[1] if len(parts) > 1 else ''}")

    if cmd in ("/start", "/help"):
        handle_help_command(tg, chat_id)
    elif cmd == "/profit":
        if len(parts) < 2:
            tg.send_message(
                chat_id,
                "กรุณาระบุชื่อย่อหุ้น เช่น <code>/profit CPALL</code>"
            )
        else:
            symbol = parts[1].upper().strip()
            try:
                handle_profit_command(tg, chat_id, symbol)
            except Exception as e:
                print(f"  ❌ Error handling /profit {symbol}: {e}")
                tg.send_message(
                    chat_id,
                    f"⚠️ เกิดข้อผิดพลาดในการประมวลผล {symbol}"
                )


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN env var not set")
        sys.exit(1)

    tg = TelegramClient(TELEGRAM_BOT_TOKEN)

    # Verify token
    try:
        me = tg.get_me()
        print(f"✓ Bot OK: @{me['result']['username']}")
    except Exception as e:
        print(f"❌ Bot token invalid: {e}")
        sys.exit(1)

    state = load_state()
    last_update_id = state.get("last_update_id", 0)

    print(f"Polling for updates (offset={last_update_id + 1 if last_update_id else 'none'})...")

    # Get updates with offset=last_update_id+1 to skip already-processed
    offset = last_update_id + 1 if last_update_id else None
    updates = tg.get_updates(offset=offset, timeout=0)

    if not updates:
        print("No new updates.")
        return

    print(f"Processing {len(updates)} updates...")
    for upd in updates:
        upd_id = upd.get("update_id", 0)
        print(f"\nUpdate #{upd_id}")
        try:
            process_update(upd, tg)
        except Exception as e:
            print(f"  ❌ Error: {e}")

        # Track the highest update_id we've seen
        if upd_id > state["last_update_id"]:
            state["last_update_id"] = upd_id

    save_state(state)
    print(f"\n✓ Saved state: last_update_id={state['last_update_id']}")


if __name__ == "__main__":
    main()
