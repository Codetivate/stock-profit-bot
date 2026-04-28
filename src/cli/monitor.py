"""Monitor SET for newly-filed news per symbol, trigger downstream actions.

On every tick (invoked by .github/workflows/monitor.yml cron or locally):
  1. Load the SET50 watchlist + the per-symbol cursor from data/state/.
  2. For each symbol, fetch news posted after the cursor.
  3. Classify each new item:
     • financial_statement → re-ingest the symbol's financials (downloads
       the new zip into data/raw/, refreshes data/processed/financials.json)
       and push an updated chart to Telegram if TELEGRAM_CHAT_ID is set.
     • any other material type (non-noise) → append to announcements.json
       and optionally send a brief text alert to Telegram.
  4. Advance the cursor to the newest datetime seen.

The cursor is datetime-based (not id-based) because SET mixed two id formats
in 2566 — numeric sort would break. ISO timestamps sort lexicographically.

Env vars:
  TELEGRAM_BOT_TOKEN          required for Telegram sends
  TELEGRAM_CHAT_ID            channel / DM to receive notifications
                              (if unset, monitor runs data-only)
  MONITOR_LOOKBACK_DAYS       how far back to scan each tick (default 3)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.cli.ingest_financials import ingest_symbol
from src.cli.ingest_news import ingest_symbol_news
from src.ingest.browser import SetSession
from src.ingest.set_api import NewsItem, fetch_news_tape, search_news
from src.ingest.zip_downloader import parse_headline
from src.parse.news_classifier import classify, extract_related_symbols
from src.cli.ingest_news import EXCLUDED_TYPES  # financial_statement et al.

from telegram_client import TelegramClient
from make_chart_html import make_chart
from command_handler import (
    build_rich_caption,
    find_latest_quarter,
    get_company_name,
    load_symbol_history,
)


STATE_PATH = Path("data/state/news_cursor.json")
SET50_PATH = Path("reference/set50.json")
FILING_WINDOW_PATH = Path("reference/current_filing_window.json")


def _load_active_filings() -> list[tuple[int, str]]:
    """Return the list of (thai_year, period) tuples currently accepted
    for Telegram notification. If the config file is missing or empty,
    returns an empty list which means "notify for all filings"."""
    if not FILING_WINDOW_PATH.exists():
        return []
    try:
        data = json.loads(FILING_WINDOW_PATH.read_text(encoding="utf-8"))
        return [
            (int(f["thai_year"]), str(f["period"]))
            for f in data.get("active_filings", [])
        ]
    except Exception as e:
        print(f"  ⚠ failed to read {FILING_WINDOW_PATH}: {e}")
        return []


def _is_in_active_filing_window(headline: str, active: list[tuple[int, str]]) -> bool:
    """True if headline matches any entry in active_filings, or if the
    window list is empty (meaning "no filter — notify for anything")."""
    if not active:
        return True
    parsed = parse_headline(headline)
    if not parsed:
        return False
    return parsed in active


def _load_watchlist() -> List[str]:
    data = json.loads(SET50_PATH.read_text(encoding="utf-8"))
    return list(data.get("symbols") or [])


def _load_cursor() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"schema_version": 1, "per_symbol": {}}


def _save_cursor(cursor: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cursor["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE_PATH.write_text(
        json.dumps(cursor, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _classify_and_partition(
    new_items: List[NewsItem],
) -> tuple[List[NewsItem], List[tuple[NewsItem, str]]]:
    """Split new items into (financials, other-material)."""
    financials: List[NewsItem] = []
    other: List[tuple[NewsItem, str]] = []
    for item in new_items:
        kind = classify(item.headline)
        if kind == "financial_statement":
            financials.append(item)
        elif kind not in EXCLUDED_TYPES:
            other.append((item, kind))
    return financials, other


def _format_news_caption(symbol: str, item: NewsItem, kind: str) -> str:
    rel = extract_related_symbols(item.headline, symbol)
    rel_line = f"\n🔗 เกี่ยวข้อง: {', '.join(rel)}" if rel else ""
    return (
        f"📰 <b>{symbol}</b>  ·  <i>{kind}</i>\n"
        f"📅 {item.date}\n\n"
        f"{item.headline}\n"
        f"{rel_line}\n"
        f'<a href="{item.url}">อ่านต้นฉบับ</a>'
    )


def _send_updated_chart(
    tg: Optional[TelegramClient],
    chat_id: Optional[str],
    symbol: str,
    item: NewsItem,
):
    """Regenerate the chart from refreshed financials.json and send it."""
    history = load_symbol_history(symbol)
    if not history:
        print(f"    ⚠ No history loaded for {symbol}; skip chart")
        return

    latest_year, latest_q = find_latest_quarter(history)
    if not latest_year or not latest_q:
        print(f"    ⚠ Incomplete history for {symbol}; skip chart")
        return

    png = make_chart(
        symbol=symbol,
        company_name=get_company_name(symbol),
        history=history,
        latest_year=latest_year,
        latest_quarter=latest_q,
        report_date=item.date,
        period_label=f"{latest_q}/{latest_year}",
    )

    caption = build_rich_caption(
        symbol=symbol,
        history=history,
        latest_year=latest_year,
        latest_quarter=latest_q,
        report_date=item.date,
        header_prefix=f"🆕 <b>งบใหม่</b>  ·  <i>{item.headline[:80]}</i>",
    )

    # Archive under data/derived/
    derived = Path("data/derived") / symbol / "charts"
    derived.mkdir(parents=True, exist_ok=True)
    chart_path = derived / f"{latest_year}_{latest_q}.png"
    chart_path.write_bytes(png)
    print(f"    📊 Chart saved: {chart_path}")

    if tg and chat_id:
        # Dual-target broadcast: TELEGRAM_CHAT_ID (private DM, debug)
        # + optional TELEGRAM_CHANNEL_ID (public channel for subscribers).
        # Failures on one target don't block the other — one bot send →
        # one telegram POST per recipient, so the photo bytes are reused.
        targets = [chat_id]
        channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
        if channel_id and channel_id != chat_id:
            targets.append(channel_id)
        for tgt in targets:
            try:
                tg.send_photo(
                    chat_id=tgt,
                    photo_bytes=png,
                    caption=caption,
                    filename=f"{symbol}_{latest_year}{latest_q}.png",
                )
                print(f"    📤 Chart sent to Telegram ({tgt})")
            except Exception as e:
                print(f"    ⚠ Failed sending to {tgt}: {e}")


def _process_new_financials(
    session: SetSession,
    symbol: str,
    financials: List[NewsItem],
    tg: Optional[TelegramClient],
    chat_id: Optional[str],
):
    """Re-run the financials ingester (idempotent), then optionally push a
    chart to Telegram — but only if the filing matches the configured
    "active filing window". This prevents out-of-window reports (late
    amendments of old quarters, for example) from spamming the chat."""
    if not financials:
        return

    print(f"    → {len(financials)} new financial filing(s) — re-ingesting…")
    # Reuse the monitor's SetSession — Playwright sync forbids nesting.
    ingest_symbol(symbol, today=date.today(), session=session)

    # Decide which filings deserve a Telegram notification.
    active = _load_active_filings()
    notify = [f for f in financials if _is_in_active_filing_window(f.headline, active)]
    if not notify:
        print(f"    ℹ none of the filings matched the active window "
              f"{active or '*'} — ingested but not notifying")
        return

    # Send one chart for the most recent filing (others will be reflected
    # in the same regenerated chart anyway).
    latest = max(notify, key=lambda n: n.datetime)
    _send_updated_chart(tg, chat_id, symbol, latest)


def _process_new_announcements(
    symbol: str,
    items: List[tuple[NewsItem, str]],
    tg: Optional[TelegramClient],
    chat_id: Optional[str],
    session: Optional[SetSession] = None,
):
    """Refresh announcements.json so the data layer stays current, but
    do NOT push them to Telegram for now — the user asked to keep the
    feed focused exclusively on financial-statement filings until news
    notifications are explicitly turned on (env ``MONITOR_PUSH_NEWS=1``
    re-enables the Telegram push side without restoring the noisy
    default)."""
    if not items:
        return
    print(f"    → {len(items)} material announcement(s) — recording only "
          f"(news push disabled)")

    # Refresh announcements.json (reuses monitor's session)
    try:
        ingest_symbol_news(symbol, today=date.today(), session=session)
    except Exception as e:
        print(f"    ⚠ announcements refresh failed: {e}")

    # Push to Telegram only when explicitly opted-in. Default OFF.
    if os.environ.get("MONITOR_PUSH_NEWS", "0").strip() not in ("1", "true", "yes"):
        return
    if tg and chat_id:
        for item, kind in items:
            try:
                tg.send_message(chat_id, _format_news_caption(symbol, item, kind))
                print(f"    📤 {kind}: {item.headline[:50]}")
            except Exception as e:
                print(f"    ⚠ Telegram send failed for {item.news_id}: {e}")


def _gather_per_symbol(
    session: SetSession,
    symbols: List[str],
    from_date: date,
    today: date,
    report: dict,
) -> dict:
    """One API call per symbol. O(N) calls for N watchlist entries."""
    out: dict[str, List[NewsItem]] = {}
    for symbol in symbols:
        try:
            out[symbol] = search_news(session, symbol, from_date, today, today=today)
        except Exception as e:
            report["errors"].append(f"{symbol}: news fetch — {e}")
            print(f"  {symbol}: ERROR fetching news: {e}")
            out[symbol] = []
    return out


def _gather_from_tape(
    session: SetSession,
    symbols: List[str],
    from_date: date,
    today: date,
    report: dict,
) -> dict:
    """ONE API call for the whole market. O(1) regardless of watchlist size."""
    try:
        tape = fetch_news_tape(session, from_date, today)
    except Exception as e:
        report["errors"].append(f"tape: news fetch — {e}")
        print(f"  TAPE ERROR: {e}")
        return {s: [] for s in symbols}

    watchlist = set(symbols)
    out: dict[str, List[NewsItem]] = {s: [] for s in symbols}
    untracked_hits = 0
    for item in tape:
        if item.symbol in watchlist:
            out[item.symbol].append(item)
        else:
            untracked_hits += 1

    total_matched = sum(len(v) for v in out.values())
    print(f"  tape fetched {len(tape)} items  ·  "
          f"matched watchlist: {total_matched}  ·  "
          f"untracked: {untracked_hits}")
    return out


def _one_tick(
    session: SetSession,
    symbols: List[str],
    from_date: date,
    today: date,
    cursor: dict,
    tg: Optional[TelegramClient],
    chat_id: Optional[str],
    dry_run: bool,
    report: dict,
    *,
    use_tape: bool = False,
):
    """Run a single monitor tick. `use_tape=True` takes one market-wide
    fetch and filters locally; otherwise hits the per-symbol endpoint."""
    news_by_symbol = (
        _gather_from_tape(session, symbols, from_date, today, report)
        if use_tape
        else _gather_per_symbol(session, symbols, from_date, today, report)
    )

    for symbol in symbols:
        news = news_by_symbol.get(symbol, [])
        prev = cursor["per_symbol"].get(symbol) or {}
        last_dt = prev.get("last_seen_datetime", "")

        new_items = [n for n in news if n.datetime > last_dt] if last_dt else news

        if not new_items:
            # Only log per-symbol when we actually probed per symbol;
            # in tape mode, "up to date" lines for 50+ symbols are noise.
            if not use_tape:
                print(f"  {symbol}: up to date")
            continue

        print(f"  {symbol}: {len(new_items)} new item(s) since {last_dt or 'first run'}")

        financials, other = _classify_and_partition(new_items)

        try:
            if not dry_run:
                _process_new_financials(session, symbol, financials, tg, chat_id)
                _process_new_announcements(symbol, other, tg, chat_id,
                                            session=session)
        except Exception as e:
            traceback.print_exc()
            report["errors"].append(f"{symbol}: processing — {e}")

        report["new_financials"] += len(financials)
        report["new_announcements"] += len(other)
        report["processed"] += 1

        # Advance cursor to newest item seen this tick
        if news:
            newest = max(news, key=lambda n: n.datetime)
            cursor["per_symbol"][symbol] = {
                "last_seen_datetime": newest.datetime,
                "last_seen_news_id": newest.news_id,
                "last_checked_at": datetime.now(timezone.utc)
                                           .isoformat(timespec="seconds"),
            }


def monitor(
    *,
    symbols: Optional[List[str]] = None,
    lookback_days: Optional[int] = None,
    today: Optional[date] = None,
    dry_run: bool = False,
    loop: bool = False,
    interval_seconds: int = 30,
    tape: bool = False,
) -> dict:
    """Single-tick or continuous-loop monitoring.

    loop=True runs forever, polling every `interval_seconds`. The browser
    session is warmed once and reused across ticks so latency per tick is
    ~1-3s per symbol instead of ~5s for session bootstrap.

    loop=False runs one tick and returns — matches the GitHub Actions
    cron usage.
    """
    today = today or date.today()
    lookback_days = lookback_days or int(os.environ.get("MONITOR_LOOKBACK_DAYS", "3"))
    symbols = symbols or _load_watchlist()
    if not symbols:
        print("No symbols in watchlist (reference/set50.json). Nothing to do.")
        return {"processed": 0}

    # Telegram client is optional
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    tg: Optional[TelegramClient] = None
    if token and chat_id and not dry_run:
        tg = TelegramClient(token)

    cursor = _load_cursor()
    report = {"processed": 0, "new_financials": 0, "new_announcements": 0, "errors": []}

    mode = f"LOOP every {interval_seconds}s" if loop else "SINGLE"
    fetch_mode = "TAPE (market-wide)" if tape else "PER-SYMBOL"
    print(f"Monitor  ·  {mode}  ·  {fetch_mode}  ·  {len(symbols)} symbols  ·  "
          f"lookback {lookback_days}d"
          f"{'  (DRY RUN)' if dry_run else ''}")
    if chat_id:
        print(f"  Telegram target: {chat_id}")
    else:
        print(f"  Telegram: disabled (no TELEGRAM_CHAT_ID)")

    def run_tick(session: SetSession):
        tick_start = time.monotonic()
        today_for_tick = date.today()  # refresh each tick for long-running loops
        from_date = today_for_tick - timedelta(days=lookback_days)
        print(f"\n── tick {datetime.now().strftime('%H:%M:%S')} ──")
        _one_tick(session, symbols, from_date, today_for_tick,
                  cursor, tg, chat_id, dry_run, report, use_tape=tape)
        if not dry_run:
            _save_cursor(cursor)
        print(f"  ⏱ tick took {time.monotonic() - tick_start:.1f}s")

    with SetSession(warm_symbol=symbols[0]) as session:
        if not loop:
            run_tick(session)
        else:
            try:
                while True:
                    try:
                        run_tick(session)
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        # Log and keep looping — a transient SET outage
                        # shouldn't kill the daemon.
                        print(f"  ⚠ tick failed: {e}")
                        traceback.print_exc()
                    time.sleep(interval_seconds)
            except KeyboardInterrupt:
                print("\nStopping on Ctrl+C.")

    print(f"\nSummary: {report}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", action="append",
                    help="Override watchlist; repeat for multiple symbols.")
    ap.add_argument("--all", action="store_true",
                    help="Use the full SET+mai universe as watchlist "
                         "(reference/set_all.json + reference/mai.json). "
                         "Pair with --tape for a single market-wide call "
                         "per tick — same cost as SET50 monitoring.")
    ap.add_argument("--lookback", type=int, default=None, help="Days to scan back")
    ap.add_argument("--today", help="Override 'today' (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + classify but do not write state, re-ingest, "
                         "or send to Telegram.")
    ap.add_argument("--loop", action="store_true",
                    help="Poll continuously (Ctrl+C to stop). Use for low-"
                         "latency detection when the PC can stay up.")
    ap.add_argument("--interval", type=int, default=30,
                    help="Loop polling interval in seconds (default 30). "
                         "Ignored if --loop is not set.")
    ap.add_argument("--tape", action="store_true",
                    help="Use the market-wide news tape (one API call for "
                         "all symbols) instead of polling each symbol "
                         "individually. Dramatically faster at SET50+ "
                         "scale; tick cost stays ~1s regardless of "
                         "watchlist size.")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else None

    symbols = args.symbol
    if args.all and not symbols:
        # Union of SET-listed and mai-listed stocks. Use a sorted list
        # so the per-symbol probe order is stable across ticks (helps
        # rate-limit handling and log readability).
        ref_set = json.loads(
            Path("reference/set_all.json").read_text(encoding="utf-8")
        ).get("symbols", [])
        ref_mai = json.loads(
            Path("reference/mai.json").read_text(encoding="utf-8")
        ).get("symbols", [])
        symbols = sorted(set(ref_set) | set(ref_mai))
        print(f"--all → loaded {len(symbols)} symbols (SET+mai)")

    monitor(
        symbols=symbols,
        lookback_days=args.lookback,
        today=today,
        dry_run=args.dry_run,
        loop=args.loop,
        interval_seconds=args.interval,
        tape=args.tape,
    )


if __name__ == "__main__":
    main()
