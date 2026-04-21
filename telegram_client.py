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
                   filename: str = "chart.png") -> Dict:
        """Send a photo as bytes with optional caption."""
        url = f"{self.base_url}/sendPhoto"
        files = {
            "photo": (filename, photo_bytes, "image/png"),
        }
        data = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        r = requests.post(url, data=data, files=files, timeout=60)
        r.raise_for_status()
        return r.json()

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
    """Build HTML-formatted caption for a financial report broadcast."""
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
        if shareholder_profit_prior and shareholder_profit_prior > 0:
            yoy = (shareholder_profit - shareholder_profit_prior) / shareholder_profit_prior * 100
            arrow = "▲" if yoy >= 0 else "▼"
            lines.append(f"📊 <b>YoY:</b> {arrow} {yoy:+.2f}%")

    if revenue is not None:
        lines.append(f"📈 <b>รายได้:</b> {revenue:,.2f} ล้านบาท")
        if revenue_prior and revenue_prior > 0:
            rev_yoy = (revenue - revenue_prior) / revenue_prior * 100
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
