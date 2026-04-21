# Stock Profit Bot 📊

A Telegram bot that broadcasts Thai stock financial reports and responds to `/profit SYMBOL` commands with beautiful quarterly breakdown charts.

---

## 🎯 What it does

- **Broadcasts** new earnings reports to a Telegram channel as soon as they're filed on SET
- **Responds** to `/profit CPALL` (or any symbol in the whitelist) in DM with a detailed quarterly chart
- **Accumulates** historical data automatically as new reports come in
- **Fully free** — runs on GitHub Actions (no server needed)

---

## 📁 Project structure

```
stock-bot/
├── parsers/
│   └── parse_set_zip.py        # Extract data from a single SET zip
├── data/                       # Accumulated history (auto-updated)
│   ├── CPALL.json
│   ├── broadcast_state.json
│   └── command_state.json
├── .github/workflows/
│   ├── broadcast.yml           # Cron: check SET every 10 min, post new reports
│   └── commands.yml            # Cron: poll /profit commands every 3 min
├── download_set_history.py     # One-time bulk downloader
├── parse_all.py                # Batch parse all downloaded zips
├── make_chart.py               # Generate v5-style chart (PNG bytes)
├── telegram_client.py          # Minimal Telegram API client
├── broadcast.py                # Main: detect new reports, post to channel
├── command_handler.py          # Main: respond to /profit commands
├── whitelist.json              # Symbols to monitor (edit to add more)
└── requirements.txt
```

---

## 🚀 Setup (15 minutes)

### 1️⃣ Clone/create the repo

```bash
mkdir stock-profit-bot && cd stock-profit-bot
# Copy all files from the zip here
git init
git add .
git commit -m "Initial bot"
```

Then push to **GitHub** (create new repo, e.g. `stock-profit-bot`):
```bash
git remote add origin https://github.com/YOUR_USERNAME/stock-profit-bot.git
git branch -M main
git push -u origin main
```

### 2️⃣ Create the Telegram bot

1. Message `@BotFather` on Telegram → `/newbot` → give it a name
2. Copy the bot token (e.g. `8637948449:AAHa...`)
3. **⚠️ Keep this secret!** Never commit to git.

### 3️⃣ Create the Telegram channel

1. Create a new public channel in Telegram (e.g. `@CPALLEarnings`)
2. Add your bot as **admin** with "Post Messages" permission
   - Use the **mobile app** — web version has a bot search bug
3. Send a test message → get the chat_id:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":-1001234567890,...`

### 4️⃣ Set GitHub Secrets

In your repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `8637948449:AAHa...` |
| `TELEGRAM_CHAT_ID` | `-1001234567890` |

### 5️⃣ Install dependencies locally (for testing)

```bash
pip install -r requirements.txt
```

### 6️⃣ Populate historical data for CPALL

Since SET doesn't have a public API, you need to seed history manually:

**Option A** (automated, may fail due to JS-rendered pages):
```bash
python download_set_history.py CPALL
```

**Option B** (manual, 100% works):
1. Open: https://www.set.or.th/th/market/product/stock/quote/CPALL/news
2. Find "งบการเงิน" (Financial Statements) entries going back 4 years
3. Click each → "ดาวน์โหลด" → get the zip
4. Save all zips to `downloads/CPALL/`
5. You should end up with **~16 zips** (4 quarters × 4 years)

Then parse all:
```bash
python parse_all.py CPALL
```

Expected output:
```
Year     Q1           Q2           Q3           Q4           Sum
2565     3,453.03     3,004.02     3,676.93     3,137.73    13,271.71
2566     4,122.78     4,438.41     4,424.29     5,496.66    18,482.14
2567     6,319.40     6,239.48     5,607.86     7,179.10    25,345.84
2568     7,585.24     6,768.46     6,596.53     7,255.88    28,206.11
```

Commit the generated `data/CPALL.json`:
```bash
git add data/CPALL.json
git commit -m "Seed CPALL history"
git push
```

### 7️⃣ Test the chart generator

```bash
python make_chart.py
```
This writes `test_chart_cpall.png` to current folder. Open it to check.

### 8️⃣ Test the bot locally

```bash
# Linux / Mac
export TELEGRAM_BOT_TOKEN="8637948449:AAHa..."
export TELEGRAM_CHAT_ID="-1001234567890"

# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN="8637948449:AAHa..."
$env:TELEGRAM_CHAT_ID="-1001234567890"

# Test command handler (poll for /profit messages)
python command_handler.py

# Test broadcast (scans SET for new reports)
python broadcast.py
```

### 9️⃣ Enable GitHub Actions

In your repo → **Actions tab** → "I understand my workflows" → Enable.

The two workflows will run automatically:
- **Broadcast**: every 10 min, weekdays 09:00-22:00 ICT
- **Commands**: every 3 min, 24/7

You can also trigger manually via **Actions → [workflow] → Run workflow**.

---

## 💬 Using the bot

### In the channel
Subscribers will automatically see new earnings reports as they're filed.

### In DM to the bot

Users can send:
```
/profit CPALL
/profit PTT
/help
```

The bot replies with the full chart + caption.

---

## 🛠️ Customization

### Add more symbols

Edit `whitelist.json`:
```json
["CPALL", "PTT", "AOT", "SCB", "KBANK", "BDMS"]
```

Then for each new symbol, seed history (repeat step 6 above).

### Adjust schedule

Edit `.github/workflows/broadcast.yml`:
```yaml
schedule:
  - cron: "*/10 2-15 * * 1-5"  # current: every 10 min, weekdays 9-22 ICT
```

Cron format: `minute hour day month dayofweek` (UTC).

### Change chart styling

Edit `make_chart.py` — all colors are defined at the top:
```python
NAVY = "#0A2540"
GREEN = "#059669"
RED = "#DC2626"
# ...
```

---

## ⚠️ Known limitations

1. **SET news page is JavaScript-heavy** — `broadcast.py` uses simple `requests` which may miss newly-filed reports. For 100% reliability, consider:
   - Using Playwright (like the TFEX scraper)
   - Subscribing to SET's push notifications (if available)
   - Manual seeding + `/profit` command on demand

2. **Historical data requires manual seeding** — SET provides 4+ years of financial statement zips on their news page, but you need to download them once.

3. **Command handler uses long-polling via cron** — there's up to 3 min latency for `/profit` replies. For instant replies, host `command_handler.py` on a VPS and use a proper webhook.

---

## 🔐 Security notes

- **Never commit bot tokens to git.** Always use GitHub Secrets.
- If you accidentally leak a token, revoke immediately via `@BotFather` → `/mybots` → Revoke.
- The `data/` directory is committed (so state persists between runs), but never contains secrets.

---

## 📬 License

MIT. Use it, fork it, improve it.
