"""High-concurrency Telegram bot server.

Designed to stay responsive when tens of thousands of users query at
once on a single Always-Free VM. The architecture is a standard
producer/consumer pipeline:

    main thread (long-poll)
        │  one updates payload at a time, timeout=30s
        ▼
    FIFO queue (bounded)
        │  workers pop and process in parallel
        ▼
    N worker threads
        ├── look up response cache (TTL 10 min)
        │     hit  → send photo (≈ 50 ms)
        │     miss → generate chart (≈ 2 s) → cache it → send photo
        └── every send goes through a token-bucket rate limiter so
            outgoing Telegram API calls never exceed its 30 msg/sec cap

Load profile:
- 4 workers × ~2 s/chart-gen = 2 fresh charts/sec when cold
- 10-minute cache means repeat requests (most popular symbols) hit
  the cache and serve in tens of milliseconds
- 50k-deep queue absorbs bursts without dropping requests; if it
  ever fills, the overflow reply tells the user to wait and retry
- No external services — cache + queue are in-process, fine for a
  single VM, recover state by restarting (acceptable for a stateless
  chart server)

Run:
    python -m src.bot.server --workers 4 --cache-ttl 600
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from command_handler import (  # noqa: E402
    _looks_like_symbol,
    build_rich_caption,
    find_latest_quarter,
    get_company_name,
    handle_help_command,
    load_state,
    load_symbol_history,
    save_state,
)
from make_chart_html import make_chart  # noqa: E402
from telegram_client import TelegramClient  # noqa: E402


# ─────────────────────────── TTL cache ────────────────────────────
class TTLCache:
    """Small thread-safe cache with per-entry TTL and size cap.

    Zero external deps (cachetools would be cleaner, but this keeps
    the production VM install minimal)."""

    def __init__(self, max_size: int = 500, ttl_seconds: int = 600):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._data: dict = {}      # key → (expiry_epoch, value)
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expiry, value = entry
            if expiry <= time.time():
                self._data.pop(key, None)
                return None
            return value

    def put(self, key, value):
        with self._lock:
            now = time.time()
            if len(self._data) >= self.max_size:
                # Evict expired first
                for k in [k for k, (exp, _) in self._data.items() if exp <= now]:
                    self._data.pop(k, None)
                # Still over? Drop the oldest by expiry.
                if len(self._data) >= self.max_size:
                    oldest = min(self._data, key=lambda k: self._data[k][0])
                    self._data.pop(oldest)
            self._data[key] = (now + self.ttl, value)


# ─────────────────────── Token-bucket limiter ─────────────────────
class TokenBucket:
    """Classic token-bucket rate limiter.

    Tokens refill at `rate` per second up to `capacity`. acquire()
    blocks until a token is available. Thread-safe."""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
            # Outside the lock — yield so other threads can also refill.
            time.sleep(0.04)


# ───────────────────────── Server config ──────────────────────────
@dataclass
class ServerConfig:
    token: str
    num_workers: int = 4
    queue_maxsize: int = 50_000
    cache_size: int = 500
    cache_ttl_seconds: int = 600      # 10 min
    send_rate_per_sec: float = 25.0   # under Telegram's 30/s cap
    send_burst: int = 30
    longpoll_timeout: int = 30
    state_save_interval: int = 10     # save cursor every N updates


# ───────────────────────── Bot server ─────────────────────────────
class BotServer:
    def __init__(self, config: ServerConfig):
        self.cfg = config
        self.tg = TelegramClient(config.token)
        self.queue: "queue.Queue[dict]" = queue.Queue(maxsize=config.queue_maxsize)
        self.cache = TTLCache(config.cache_size, config.cache_ttl_seconds)
        self.rate = TokenBucket(config.send_rate_per_sec, config.send_burst)
        self.stop_flag = threading.Event()
        self.stats = {
            "received": 0, "processed": 0,
            "cache_hits": 0, "cache_misses": 0,
            "errors": 0, "overflow": 0,
        }
        self.stats_lock = threading.Lock()
        self.state = load_state()
        self.updates_since_save = 0
        self.state_lock = threading.Lock()

    # ── orchestration ────────────────────────────────────────────
    def start(self):
        me = self.tg.get_me()
        username = me.get("result", {}).get("username", "?")
        print(f"BotServer  ·  @{username}  ·  workers={self.cfg.num_workers}  "
              f"queue={self.cfg.queue_maxsize}  cache_ttl={self.cfg.cache_ttl_seconds}s  "
              f"rate={self.cfg.send_rate_per_sec}/s")
        for i in range(self.cfg.num_workers):
            threading.Thread(
                target=self._worker_loop,
                name=f"worker-{i}",
                daemon=True,
            ).start()
        threading.Thread(
            target=self._stats_loop,
            name="stats",
            daemon=True,
        ).start()
        self._main_loop()

    # ── main loop: long-poll → enqueue ───────────────────────────
    def _main_loop(self):
        last_id = self.state.get("last_update_id", 0)
        offset: Optional[int] = last_id + 1 if last_id else None

        while not self.stop_flag.is_set():
            try:
                updates = self.tg.get_updates(
                    offset=offset, timeout=self.cfg.longpoll_timeout
                )
            except KeyboardInterrupt:
                print("\nStopping on Ctrl+C.")
                self.stop_flag.set()
                break
            except Exception as e:
                print(f"⚠ poll error: {e}")
                time.sleep(5)
                continue

            if not updates:
                continue

            for upd in updates:
                uid = upd.get("update_id", 0)
                if uid > last_id:
                    last_id = uid
                try:
                    self.queue.put_nowait(upd)
                    with self.stats_lock:
                        self.stats["received"] += 1
                except queue.Full:
                    with self.stats_lock:
                        self.stats["overflow"] += 1
                    self._send_overflow_reply(upd)

            offset = last_id + 1
            with self.state_lock:
                self.state["last_update_id"] = last_id
                self.updates_since_save += len(updates)
                if self.updates_since_save >= self.cfg.state_save_interval:
                    save_state(self.state)
                    self.updates_since_save = 0

    # ── worker loop ──────────────────────────────────────────────
    def _worker_loop(self):
        while not self.stop_flag.is_set():
            try:
                upd = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._process_update(upd)
                with self.stats_lock:
                    self.stats["processed"] += 1
            except Exception as e:
                with self.stats_lock:
                    self.stats["errors"] += 1
                print(f"⚠ worker error: {e}")
                traceback.print_exc()
            finally:
                self.queue.task_done()

    # ── per-update handling ─────────────────────────────────────
    def _process_update(self, upd: dict):
        msg = upd.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            return

        if text.startswith("/"):
            cmd = text.split()[0].split("@")[0].lower()
            if cmd in ("/start", "/help"):
                self.rate.acquire()
                handle_help_command(self.tg, chat_id)
            return

        if _looks_like_symbol(text):
            self._handle_symbol(chat_id, text.upper())

    def _handle_symbol(self, chat_id, symbol):
        cached = self.cache.get(symbol)
        if cached is not None:
            png, caption, filename = cached
            with self.stats_lock:
                self.stats["cache_hits"] += 1
            self.rate.acquire()
            self.tg.send_photo(
                chat_id=chat_id, photo_bytes=png,
                caption=caption, filename=filename,
            )
            return

        with self.stats_lock:
            self.stats["cache_misses"] += 1

        history = load_symbol_history(symbol)
        if not history:
            self.rate.acquire()
            self.tg.send_message(
                chat_id,
                f"❌ ยังไม่มีข้อมูลของ <b>{symbol}</b>\n"
                f"ระบบจะเก็บข้อมูลเมื่อมีงบใหม่ออก"
            )
            return

        latest_year, latest_q = find_latest_quarter(history)
        if not latest_year or not latest_q:
            self.rate.acquire()
            self.tg.send_message(chat_id, f"❌ ข้อมูล {symbol} ยังไม่สมบูรณ์")
            return

        # Load source file for company metadata and report date
        path = Path("data/processed") / symbol / "financials.json"
        if not path.exists():
            path = Path("data") / f"{symbol}.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        company_name = raw.get("company_name_en") or get_company_name(symbol)
        report_date = raw.get("updated_at", "")
        if report_date:
            try:
                report_date = datetime.strptime(report_date, "%Y-%m-%d") \
                                      .strftime("%d %b %Y")
            except Exception:
                pass

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
        filename = f"{symbol}_{latest_year}{latest_q}.png"

        self.cache.put(symbol, (png, caption, filename))

        self.rate.acquire()
        self.tg.send_photo(
            chat_id=chat_id, photo_bytes=png,
            caption=caption, filename=filename,
        )

    def _send_overflow_reply(self, upd):
        chat_id = ((upd.get("message") or {}).get("chat") or {}).get("id")
        if not chat_id:
            return
        try:
            self.rate.acquire()
            self.tg.send_message(
                chat_id,
                "⚠️ ระบบยุ่งอยู่ — คิวเต็มชั่วคราว\nลองใหม่อีก 10 วินาที"
            )
        except Exception:
            pass

    # ── stats ────────────────────────────────────────────────────
    def _stats_loop(self):
        last_snapshot = None
        while not self.stop_flag.is_set():
            time.sleep(30)
            with self.stats_lock:
                s = dict(self.stats)
            s["qsize"] = self.queue.qsize()
            if s != last_snapshot:
                total = s["cache_hits"] + s["cache_misses"]
                hit_rate = (s["cache_hits"] * 100 // total) if total else 0
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"rcvd={s['received']} proc={s['processed']} "
                    f"hit={s['cache_hits']} miss={s['cache_misses']} "
                    f"hit%={hit_rate} err={s['errors']} "
                    f"ovfl={s['overflow']} qsize={s['qsize']}"
                )
                last_snapshot = s


# ───────────────────────── Entry point ────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4,
                    help="Number of worker threads (default 4).")
    ap.add_argument("--queue-size", type=int, default=50_000,
                    help="Backpressure limit before overflow (default 50000).")
    ap.add_argument("--cache-ttl", type=int, default=600,
                    help="Response cache TTL seconds (default 600 = 10 min).")
    ap.add_argument("--cache-size", type=int, default=500,
                    help="Max cached symbols (default 500).")
    ap.add_argument("--rate", type=float, default=25.0,
                    help="Outbound msg/sec (Telegram caps at 30).")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN env var not set")
        sys.exit(1)

    config = ServerConfig(
        token=token,
        num_workers=args.workers,
        queue_maxsize=args.queue_size,
        cache_ttl_seconds=args.cache_ttl,
        cache_size=args.cache_size,
        send_rate_per_sec=args.rate,
    )
    server = BotServer(config)
    try:
        server.start()
    except KeyboardInterrupt:
        print("Stopped")


if __name__ == "__main__":
    main()
