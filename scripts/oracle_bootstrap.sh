#!/usr/bin/env bash
# One-shot bootstrap for the Oracle Cloud Always-Free VM.
#
# Run AFTER you SSH in. It:
#   1. installs system deps + Python venv
#   2. clones or updates the repo under ~/stock-profit-bot
#   3. installs pip requirements + Playwright chromium
#   4. prompts for your bot token + chat id, writes .env
#   5. drops two systemd units that supervise monitor + commands
#   6. starts them immediately and enables boot-time start
#
# Usage (copy-paste this one line on the VM):
#   curl -fsSL https://raw.githubusercontent.com/Codetivate/stock-profit-bot/main/scripts/oracle_bootstrap.sh | bash
#
# Re-running is safe — it's idempotent.

set -euo pipefail

REPO_URL="https://github.com/Codetivate/stock-profit-bot.git"
INSTALL_DIR="${HOME}/stock-profit-bot"
PY_BIN="python3"

echo "══════════════════════════════════════════════════════════"
echo "  stock-profit-bot — Oracle Cloud bootstrap"
echo "══════════════════════════════════════════════════════════"

# ── 1. System deps ────────────────────────────────────────────
echo
echo "[1/6] Installing apt packages (needs sudo)…"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv git tzdata curl ca-certificates

sudo timedatectl set-timezone Asia/Bangkok || true

# ── 2. Clone or update repo ───────────────────────────────────
echo
echo "[2/6] Syncing repo at ${INSTALL_DIR}…"
if [ -d "${INSTALL_DIR}/.git" ]; then
    git -C "${INSTALL_DIR}" pull --rebase
else
    git clone "${REPO_URL}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

# ── 3. Python venv + deps + Playwright ────────────────────────
echo
echo "[3/6] Building venv + installing Python deps (2-3 min)…"
${PY_BIN} -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
python -m playwright install --with-deps chromium

# ── 4. Secrets: .env ──────────────────────────────────────────
echo
echo "[4/6] Configuring .env"
ENV_FILE="${INSTALL_DIR}/.env"
if [ -f "${ENV_FILE}" ]; then
    echo "  .env already exists — leaving it alone."
else
    read -rp "  TELEGRAM_BOT_TOKEN: " BOT_TOKEN
    read -rp "  TELEGRAM_CHAT_ID (Telegram chat_id for notifications): " CHAT_ID
    cat > "${ENV_FILE}" <<EOF
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
TELEGRAM_CHAT_ID=${CHAT_ID}
TELEGRAM_ADMIN_CHAT_ID=${CHAT_ID}
EOF
    chmod 600 "${ENV_FILE}"
    echo "  wrote ${ENV_FILE}"
fi

# ── 5. systemd units ──────────────────────────────────────────
echo
echo "[5/6] Installing systemd services…"
USER_NAME="$(whoami)"
VENV_PY="${INSTALL_DIR}/.venv/bin/python"

sudo tee /etc/systemd/system/stock-bot-monitor.service >/dev/null <<EOF
[Unit]
Description=Stock Profit Bot — SET filings monitor (tape mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
Environment=PYTHONIOENCODING=utf-8
ExecStart=${VENV_PY} -u -m src.cli.monitor --loop --tape --interval 15 --lookback 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/stock-bot-commands.service >/dev/null <<EOF
[Unit]
Description=Stock Profit Bot — Telegram command responder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
Environment=PYTHONIOENCODING=utf-8
ExecStart=${VENV_PY} -u command_handler.py --loop
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# ── 6. Enable + start ─────────────────────────────────────────
echo
echo "[6/6] Starting services…"
sudo systemctl daemon-reload
sudo systemctl enable --now stock-bot-monitor stock-bot-commands

echo
echo "══════════════════════════════════════════════════════════"
echo "  ✅ Done. Live status:"
echo "══════════════════════════════════════════════════════════"
sudo systemctl --no-pager status stock-bot-monitor | head -6
echo
sudo systemctl --no-pager status stock-bot-commands | head -6

echo
echo "Follow logs with:"
echo "  sudo journalctl -u stock-bot-monitor  -f"
echo "  sudo journalctl -u stock-bot-commands -f"
echo
echo "Test now: DM your bot with 'CPALL' — you should get a reply within seconds."
