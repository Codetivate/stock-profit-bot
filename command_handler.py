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

from make_chart_html import make_chart, QuarterlyData
from telegram_client import TelegramClient


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
STATE_FILE = Path("data/state/telegram_offset.json")
PROCESSED_DIR = Path("data/processed")
DATA_DIR = Path("data")  # legacy path fallback


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


def _financials_path(symbol: str) -> Path:
    """Resolve financials file for a symbol, preferring new structure."""
    new_path = PROCESSED_DIR / symbol / "financials.json"
    if new_path.exists():
        return new_path
    return DATA_DIR / f"{symbol}.json"  # legacy


def load_symbol_history(symbol: str):
    """Load quarterly history for a symbol, returns {year: QuarterlyData} or None."""
    path = _financials_path(symbol)
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
            full_year=qs.get("FullYear"),
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


def build_rich_caption(symbol, history, latest_year, latest_quarter,
                        report_date="", header_prefix=None):
    """Build the Telegram HTML caption that shows under the chart photo.

    The caption is the *only* thing a user sees without tapping the
    image in the chat list, so QoQ / YoY / full-year-YoY all go here
    explicitly — don't make them squint at the thumbnail.
    """
    latest = history[latest_year].get(latest_quarter)
    if latest is None:
        return f"● <b>{symbol}</b>  ·  FY{latest_year} {latest_quarter}"

    q_order = ["Q1", "Q2", "Q3", "Q4"]
    q_idx = q_order.index(latest_quarter)

    # QoQ
    if q_idx > 0:
        prev_q = history[latest_year].get(q_order[q_idx - 1])
        prev_q_label = f"{q_order[q_idx - 1]}/{latest_year}"
    else:
        prev_y_data = history.get(latest_year - 1)
        prev_q = prev_y_data.q4 if prev_y_data else None
        prev_q_label = f"Q4/{latest_year - 1}"
    qoq = ((latest - prev_q) / prev_q * 100) if prev_q else None

    # YoY
    prev_y_data = history.get(latest_year - 1)
    prev_y = prev_y_data.get(latest_quarter) if prev_y_data else None
    prev_y_label = f"{latest_quarter}/{latest_year - 1}"
    yoy = ((latest - prev_y) / prev_y * 100) if prev_y else None

    # Full-year YoY (only if the full year is complete)
    fy_sum = history[latest_year].sum()
    prev_fy = history.get(latest_year - 1)
    prev_fy_sum = prev_fy.sum() if prev_fy else None
    fy_yoy = (
        (fy_sum - prev_fy_sum) / prev_fy_sum * 100
        if fy_sum is not None and prev_fy_sum
        else None
    )

    def fmt_delta(pct):
        if pct is None:
            return None
        emoji = "🟢" if pct >= 0 else "🔴"
        arrow = "▲" if pct >= 0 else "▼"
        return emoji, arrow, pct

    lines = []
    if header_prefix:
        lines.append(header_prefix)
    lines.append(f"● <b>{symbol}</b>  ·  FY{latest_year} {latest_quarter}")
    lines.append(f"💰 <b>กำไรสุทธิ: {latest:,.2f}</b> ล้านบาท")
    lines.append("")

    if qoq is not None and prev_q is not None:
        e, a, _ = fmt_delta(qoq)
        lines.append(
            f"{e} <b>QoQ {a} {qoq:+.1f}%</b>  "
            f"<i>(vs {prev_q_label}: {prev_q:,.2f})</i>"
        )

    if yoy is not None and prev_y is not None:
        e, a, _ = fmt_delta(yoy)
        lines.append(
            f"{e} <b>YoY {a} {yoy:+.1f}%</b>  "
            f"<i>(vs {prev_y_label}: {prev_y:,.2f})</i>"
        )

    if fy_sum is not None and fy_yoy is not None:
        e, a, _ = fmt_delta(fy_yoy)
        lines.append(
            f"📊 <b>ทั้งปี {latest_year}: {fy_sum:,.2f}</b> ล้านบาท "
            f"({e} {a} {fy_yoy:+.1f}%)"
        )

    if report_date:
        lines.append("")
        lines.append(f"<i>งบเผยแพร่: {report_date}</i>")

    return "\n".join(lines)


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
    path = _financials_path(symbol)
    raw = json.loads(path.read_text(encoding="utf-8"))

    company_name = raw.get("company_name_en") or get_company_name(symbol)
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

    caption = build_rich_caption(
        symbol=symbol,
        history=history,
        latest_year=latest_year,
        latest_quarter=latest_q,
        report_date=report_date,
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
        "วิธีใช้:\n\n"
        "• พิมพ์ชื่อย่อหุ้นได้เลย เช่น <code>CPALL</code>\n"
        "• หรือใช้คำสั่ง <code>/profit CPALL</code>\n"
        "• <code>/help</code> — แสดงคำสั่งที่ใช้ได้\n\n"
        "Bot จะ broadcast งบใหม่ใน channel อัตโนมัติเมื่อมีงบออก"
    )
    tg.send_message(chat_id, msg)


def _looks_like_symbol(text: str) -> bool:
    """Heuristic: a single token, 2-10 chars, letters/digits/&-."""
    if not text or " " in text or "\n" in text:
        return False
    if not (2 <= len(text) <= 10):
        return False
    return all(c.isalnum() or c in "&-." for c in text)


def process_update(update: dict, tg: TelegramClient):
    """Process a single Telegram update."""
    msg = update.get("message", {})
    if not msg:
        return

    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()

    if not text:
        return

    # Slash commands
    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        if "@" in cmd:
            cmd = cmd.split("@")[0]

        print(f"  → Command: {cmd}  arg: {parts[1] if len(parts) > 1 else ''}")

        if cmd in ("/start", "/help"):
            handle_help_command(tg, chat_id)
        elif cmd == "/profit":
            if len(parts) < 2:
                tg.send_message(
                    chat_id,
                    "กรุณาระบุชื่อย่อหุ้น เช่น <code>/profit CPALL</code> หรือพิมพ์แค่ <code>CPALL</code>"
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
        return

    # Bare symbol (e.g. "CPALL" or "cpall")
    if _looks_like_symbol(text):
        symbol = text.upper()
        print(f"  → Symbol lookup: {symbol}")
        try:
            handle_profit_command(tg, chat_id, symbol)
        except Exception as e:
            print(f"  ❌ Error handling {symbol}: {e}")
            tg.send_message(
                chat_id,
                f"⚠️ เกิดข้อผิดพลาดในการประมวลผล {symbol}"
            )


def poll_once(tg: TelegramClient, state: dict, long_poll_timeout: int = 0):
    """One poll cycle. Processes any pending updates and saves state."""
    last_update_id = state.get("last_update_id", 0)
    offset = last_update_id + 1 if last_update_id else None
    updates = tg.get_updates(offset=offset, timeout=long_poll_timeout)

    if not updates:
        return 0

    print(f"Processing {len(updates)} updates...")
    for upd in updates:
        upd_id = upd.get("update_id", 0)
        print(f"\nUpdate #{upd_id}")
        try:
            process_update(upd, tg)
        except Exception as e:
            print(f"  ❌ Error: {e}")

        if upd_id > state["last_update_id"]:
            state["last_update_id"] = upd_id

    save_state(state)
    return len(updates)


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN env var not set")
        sys.exit(1)

    loop_mode = "--loop" in sys.argv

    tg = TelegramClient(TELEGRAM_BOT_TOKEN)

    try:
        me = tg.get_me()
        print(f"✓ Bot OK: @{me['result']['username']}")
    except Exception as e:
        print(f"❌ Bot token invalid: {e}")
        sys.exit(1)

    state = load_state()

    if not loop_mode:
        last_update_id = state.get("last_update_id", 0)
        print(f"Polling for updates (offset={last_update_id + 1 if last_update_id else 'none'})...")
        n = poll_once(tg, state, long_poll_timeout=0)
        if n == 0:
            print("No new updates.")
        else:
            print(f"\n✓ Saved state: last_update_id={state['last_update_id']}")
        return

    print("Loop mode: long-polling continuously. Ctrl+C to stop.")
    while True:
        try:
            poll_once(tg, state, long_poll_timeout=30)
        except KeyboardInterrupt:
            print("\nStopping.")
            break
        except Exception as e:
            print(f"⚠️ Poll error: {e}. Retrying in 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()
