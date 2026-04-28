"""discover_chat_ids.py — list every chat / group / channel the bot can see.

Telegram bot admins need the numeric ``chat_id`` to send messages
programmatically. The Telegram UI doesn't show it, so we ask the
bot itself: ``getUpdates`` returns recent activity, and every
``message`` / ``channel_post`` carries a ``chat.id``.

Steps for the user (one-time setup per channel/group):
  1. Add the bot as admin (with "Send messages" permission).
  2. Post ANY message in the channel/group (a single dot is fine —
     it just has to generate an update).
  3. Run this script; it prints a table of every chat the bot
     has seen recently along with the chat_id you should put in
     ``TELEGRAM_CHANNEL_ID`` / ``TELEGRAM_GROUP_ID`` env vars.

Limits:
  - ``getUpdates`` only goes back ~24 hours.
  - The bot has to receive at least one message in each chat
    after being added — Telegram doesn't expose old chat memberships.
  - DM (private) chat_ids show up too — that's how
    ``TELEGRAM_CHAT_ID`` was discovered initially.

Usage:
    python scripts/discover_chat_ids.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_client import TelegramClient  # noqa: E402


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN env var required", file=sys.stderr)
        return 2

    tg = TelegramClient(token)
    me = tg.get_me().get("result", {})
    bot_username = me.get("username", "?")
    print(f"Bot: @{bot_username}\n")

    updates = tg.get_updates()
    if not updates:
        print("No updates in the last ~24h.")
        print()
        print("To discover a chat_id:")
        print("  1. Add the bot to the channel/group as admin (with 'Send")
        print("     messages' permission). This is REQUIRED for channels.")
        print("  2. Post any message in that channel/group (even a single ")
        print("     character is enough — it just has to generate an update).")
        print("  3. Re-run this script.")
        return 1

    seen: dict[int, dict] = {}
    for u in updates:
        # `message` (DMs/groups), `channel_post` (channels) — same shape
        msg = u.get("message") or u.get("channel_post") or u.get("edited_message") \
            or u.get("edited_channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen[cid] = {
            "type": chat.get("type", "?"),  # private | group | supergroup | channel
            "title": chat.get("title") or chat.get("username") or chat.get("first_name") or "",
            "username": chat.get("username", ""),
        }

    if not seen:
        print("Updates exist but none had a chat block — nothing to show.")
        return 1

    print(f"Found {len(seen)} chat(s):\n")
    print(f"  {'CHAT_ID':>15s}  {'TYPE':10s}  {'TITLE / USERNAME'}")
    print(f"  {'-'*15}  {'-'*10}  {'-'*40}")
    for cid, info in sorted(seen.items()):
        title = info["title"]
        if info["username"]:
            title = f"{title}  (@{info['username']})"
        print(f"  {cid:>15d}  {info['type']:10s}  {title}")

    print()
    print("Tips:")
    print("  • Channels: chat_id is a NEGATIVE number (looks like -100xxx…).")
    print("  • Save it in .env as e.g.  TELEGRAM_CHANNEL_ID=-1001234567890")
    print("  • For dual-target broadcast (DM + channel) the bot reads both")
    print("    TELEGRAM_CHAT_ID and TELEGRAM_CHANNEL_ID and posts to each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
