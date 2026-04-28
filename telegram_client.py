"""
telegram_client.py — Simple Telegram Bot API client

Only needs requests library, no dependencies on python-telegram-bot.
"""
import os
import io
import json
import time
from typing import Optional, Dict, Any

import requests


class TelegramClient:
    """Minimal Telegram Bot API client for sending photos and handling commands."""

    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"

    def _request(self, method: str, **kwargs) -> Dict[str, Any]:
        """Make a GET request to the API."""
        url = f"{self.base_url}/{method}"
        r = requests.get(url, timeout=30, **kwargs)
        r.raise_for_status()
        return r.json()

    def send_message(self, chat_id: str, text: str,
                     parse_mode: str = "HTML",
                     reply_markup: Optional[dict] = None) -> Dict:
        """Send a text message."""
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)
        return self._request("sendMessage", params=params)

    def send_photo(self, chat_id: str, photo_bytes: bytes,
                   caption: str = "",
                   parse_mode: str = "HTML",
                   filename: str = "chart.png",
                   max_retries: int = 8) -> Dict:
        """Send a photo as bytes with optional caption.

        Retries automatically per the Telegram Bot API guidance:
          • 429 Too Many Requests: respect ``parameters.retry_after``
            seconds before next attempt (the server tells us exactly
            how long to wait).
          • 5xx server errors / network errors: exponential backoff
            (2s → 4s → 8s, capped at 60s).
          • 4xx client errors other than 429: surface immediately —
            retrying a malformed request would just spam the API.

        ``max_retries`` is the cap on total attempts (including the
        first), so the default 8 covers a few minutes of transient
        outage without giving up. Caller can reduce for unit tests.
        """
        url = f"{self.base_url}/sendPhoto"
        files = {
            "photo": (filename, photo_bytes, "image/png"),
        }
        data = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                r = requests.post(url, data=data, files=files, timeout=60)
            except requests.RequestException as e:
                # Network-level failure — back off and retry.
                last_err = e
                wait = min(2 ** attempt, 60)
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
                raise

            if r.status_code == 200:
                return r.json()

            # Telegram rate limit — body has parameters.retry_after.
            if r.status_code == 429:
                try:
                    payload = r.json()
                except ValueError:
                    payload = {}
                retry_after = (
                    (payload.get("parameters") or {}).get("retry_after")
                    or int(r.headers.get("Retry-After", "1"))
                )
                # Add a 1-second cushion so we don't immediately re-trip
                # the same window. Cap absurd server-supplied values so
                # a single 60s retry doesn't stretch into hours.
                wait = max(1, min(int(retry_after) + 1, 120))
                if attempt < max_retries:
                    time.sleep(wait)
                    continue

            # Transient server error — exponential backoff.
            if 500 <= r.status_code < 600:
                wait = min(2 ** attempt, 60)
                if attempt < max_retries:
                    time.sleep(wait)
                    continue

            # 4xx other than 429 → caller's request is malformed; no
            # amount of retrying will help.
            r.raise_for_status()

        # Exhausted retries without a success.
        if last_err:
            raise last_err
        raise RuntimeError(
            f"sendPhoto failed after {max_retries} attempts (last status={r.status_code})"
        )

    def get_updates(self, offset: Optional[int] = None,
                    timeout: int = 0) -> list:
        """Poll getUpdates for incoming messages/commands."""
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        res = self._request("getUpdates", params=params)
        return res.get("result", [])

    def get_me(self) -> Dict:
        """Verify bot token is valid."""
        return self._request("getMe")


def format_caption(data) -> str:
    """Build HTML-formatted caption for a financial report broadcast.

    Percentage changes use ``abs(prior)`` in the denominator so a flip
    from loss → profit registers as positive. This matches the chart and
    `command_handler.build_rich_caption` — the previous "prior must be
    > 0" guard silently dropped YoY whenever the prior period was a
    loss, and a raw ``/ prior`` would have inverted the sign.
    """
    symbol = data.get("symbol", "")
    period_label = data.get("period_label", "")
    shareholder_profit = data.get("shareholder_profit")
    revenue = data.get("revenue")
    revenue_prior = data.get("revenue_prior")
    shareholder_profit_prior = data.get("shareholder_profit_prior")

    lines = [
        f"● <b>{symbol}</b>  ·  {period_label}",
        "━━━━━━━━━━━━━━━━",
    ]

    if shareholder_profit is not None:
        lines.append(f"💰 <b>กำไรสุทธิ:</b> {shareholder_profit:,.2f} ล้านบาท")

        # YoY for profit
        if shareholder_profit_prior:
            yoy = (shareholder_profit - shareholder_profit_prior) / abs(shareholder_profit_prior) * 100
            arrow = "▲" if yoy >= 0 else "▼"
            lines.append(f"📊 <b>YoY:</b> {arrow} {yoy:+.2f}%")

    if revenue is not None:
        lines.append(f"📈 <b>รายได้:</b> {revenue:,.2f} ล้านบาท")
        if revenue_prior:
            rev_yoy = (revenue - revenue_prior) / abs(revenue_prior) * 100
            arrow = "▲" if rev_yoy >= 0 else "▼"
            lines.append(f"       (รายได้ {arrow} {rev_yoy:+.2f}% YoY)")

    lines.append("━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Set TELEGRAM_BOT_TOKEN env var to test")
        exit(1)

    client = TelegramClient(token)
    me = client.get_me()
    print(f"Bot OK: @{me['result']['username']}")
