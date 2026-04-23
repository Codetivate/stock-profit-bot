# Deploy to Oracle Cloud Always Free (24/7 monitor + bot)

Target: run both the SET filings monitor (`--tape --loop`) and the
Telegram command responder (`command_handler --loop`) on a single
Always-Free ARM VM, so detection latency stays under a minute and the
user's PC doesn't need to be on.

**Cost**: ฿0 forever (subject to Oracle keeping the Always-Free tier).
Credit card required at signup; won't be charged as long as you stay
within Always-Free limits.

---

## 1. Create the VM (30 min, one-time)

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/)
   — pick a **home region** that has ARM Ampere capacity (Singapore,
   Tokyo, and ap-mumbai-1 usually do; Bangkok none; us-ashburn-1 is
   contested — avoid).
2. In the console: **Compute → Instances → Create instance**
   - Name: `stock-profit-bot`
   - Image: **Canonical Ubuntu 24.04** (ARM-compatible)
   - Shape: **VM.Standard.A1.Flex** → **2 OCPU, 12 GB RAM**
     (Always-Free cap is 4 OCPU / 24 GB total across the tenancy,
     so you have room for two of these if you want isolation later.)
   - Networking: leave defaults, *keep* "Assign public IPv4 address"
   - SSH keys: upload your public key (generate with `ssh-keygen -t ed25519`
     if you don't have one) — save the private key somewhere safe.
   - Create.
3. Once it's *Running*, note the **Public IP**.

### Open the firewall

Always-Free VMs ship with VCN ingress locked down. The bot doesn't
need inbound traffic (it polls SET and talks to Telegram outbound
only), so you can skip this. If you later run a webhook endpoint,
add a security-list rule for TCP 80/443.

---

## 2. SSH in and install dependencies

```bash
ssh ubuntu@<PUBLIC_IP>

# System deps for Playwright Chromium on ARM Ubuntu 24.04
sudo apt update
sudo apt install -y python3-pip python3-venv git tzdata

# Optional but recommended: Bangkok time
sudo timedatectl set-timezone Asia/Bangkok
```

---

## 3. Clone the repo + set up the venv

```bash
cd ~
git clone https://github.com/Codetivate/stock-profit-bot.git
cd stock-profit-bot

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

# Chromium + all the system libs it needs (ARM64 build)
python -m playwright install --with-deps chromium
```

Verify the pipeline with a single dry-run tick:

```bash
source .venv/bin/activate
python -m src.cli.monitor --tape --dry-run --symbol CPALL --lookback 2
```

Expected:
```
Monitor  ·  SINGLE  ·  TAPE (market-wide)  ·  1 symbols  ·  lookback 2d  (DRY RUN)
  tape fetched ~400 items  ·  matched watchlist: 0  ·  untracked: ~400
  ⏱ tick took ~0.3s
```

---

## 4. Secrets — use a .env file, not env vars in unit files

```bash
# Still in ~/stock-profit-bot
cp .env.example .env
nano .env
# paste real values:
#   TELEGRAM_BOT_TOKEN=8637948449:AAHa...
#   TELEGRAM_CHAT_ID=1963584270
#   TELEGRAM_ADMIN_CHAT_ID=1963584270   # same as chat_id for solo admin
```

`.env` is gitignored, stays local to the VM.

---

## 5. systemd services — 24/7 supervision, auto-restart on crash

Create `/etc/systemd/system/stock-bot-monitor.service`:

```ini
[Unit]
Description=Stock Profit Bot — SET filings monitor (tape mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock-profit-bot
EnvironmentFile=/home/ubuntu/stock-profit-bot/.env
Environment=PYTHONIOENCODING=utf-8
ExecStart=/home/ubuntu/stock-profit-bot/.venv/bin/python -u -m src.cli.monitor --loop --tape --interval 15 --lookback 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/stock-bot-commands.service`:

```ini
[Unit]
Description=Stock Profit Bot — Telegram command responder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock-profit-bot
EnvironmentFile=/home/ubuntu/stock-profit-bot/.env
Environment=PYTHONIOENCODING=utf-8
ExecStart=/home/ubuntu/stock-profit-bot/.venv/bin/python -u command_handler.py --loop
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-bot-monitor stock-bot-commands

# Verify
sudo systemctl status stock-bot-monitor
sudo systemctl status stock-bot-commands

# Live logs
sudo journalctl -u stock-bot-monitor -f
sudo journalctl -u stock-bot-commands -f
```

---

## 6. Keep data in sync with GitHub

The services write to `data/state/` and `data/processed/` locally on
the VM. Two easy strategies:

**Option A — push state to GitHub periodically (cron, every hour)**

```bash
crontab -e
# add:
0 * * * * cd /home/ubuntu/stock-profit-bot && /usr/bin/git add data/state data/processed && /usr/bin/git diff --cached --quiet || /usr/bin/git -c user.email=bot@oracle.local -c user.name=oracle-bot commit -m "chore: sync state [skip ci]" && /usr/bin/git push 2>&1 | logger -t stock-bot-sync
```

(Requires a deploy key or PAT configured for push — see step 7.)

**Option B — VM is source of truth, GitHub only holds code**

Skip git sync. Back up `data/` nightly to Oracle Object Storage
(Always-Free includes 20 GB). Simpler, no push-conflict with the
GHA workflows.

For solo use, **Option B** is cleaner. If multiple engineers run local
runs too, **Option A** keeps everyone synced.

---

## 7. Give the VM push access to GitHub (only if using Option A)

```bash
# On the VM:
ssh-keygen -t ed25519 -C "oracle-bot" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub
# → copy this to GitHub → repo Settings → Deploy keys → Add deploy key
#   check "Allow write access"

# Configure git to use it:
cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/github_deploy
    IdentitiesOnly yes
EOF

# Switch the remote from https to ssh:
cd ~/stock-profit-bot
git remote set-url origin git@github.com:Codetivate/stock-profit-bot.git

# Test
git pull --rebase
```

---

## 8. Disable the GitHub Actions monitor (optional)

Once the Oracle VM is running reliably, the `monitor.yml` cron becomes
redundant noise. Either:

- Set `on.schedule` to nothing (keep only `workflow_dispatch`), or
- Leave it as a failsafe in case the VM ever goes down. The cost is
  zero because the repo is public.

`commands.yml` should also be disabled since `stock-bot-commands`
now owns the Telegram polling — otherwise two pollers fight for the
same `getUpdates` offset and some replies will be missed.

---

## 9. Observability checklist

On the VM:

```bash
# Is it running?
systemctl is-active stock-bot-monitor stock-bot-commands

# Last tick seen?
tail -50 /var/log/journal/$(</etc/machine-id)/user-1000.journal 2>/dev/null \
  || sudo journalctl -u stock-bot-monitor -n 30 --no-pager

# How much memory/CPU?
systemctl status stock-bot-monitor | grep -E "Memory|CPU"
```

If the monitor stops advancing the cursor or Chromium starts leaking:

```bash
sudo systemctl restart stock-bot-monitor
```

Restart is cheap — Playwright warms up in ~5s.

---

## 10. Tear-down (if you ever want to)

```bash
sudo systemctl disable --now stock-bot-monitor stock-bot-commands
sudo rm /etc/systemd/system/stock-bot-{monitor,commands}.service
```

VM itself: Oracle console → terminate instance. Always-Free resources
don't bill, but hoarding them may block other Always-Free users.
