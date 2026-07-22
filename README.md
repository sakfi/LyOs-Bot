# Lyos Telegram MiniApp Bot (LyOS)

An automated, lightweight, asynchronous Python bot designed for playing the **LyOS** Telegram MiniApp game (`https://lyos.fly.dev`).

---

## Key Bot Features
- 🎯 **Target Discovery via Scan Tab**: Automates opening the **Scan Tab** and running **Random Scans** (5 targets per batch).
- 🔍 **Target Filtering**: Filters targets strictly for **`Reputation == 0`** and **`Firewall Level >= 80`**.
- ⚡ **9–10 Concurrent Active Bypass Jobs**: Continuously scans and launches firewall bypasses to maintain 9–10 active jobs running simultaneously.
- 💎 **Highest-Level Miner Upload**: Automatically attempts to upload your highest miner (**Level 378**) and dynamically decrements levels if needed to ensure maximum yield.
- 🔒 **Log Deletion & Anonymity**: Clears target logs immediately before siphoning and after siphoning.
- 🏦 **Automated Vault/Bank Protection**: Deposits siphoned funds into the in-game Bank immediately after every hacking batch and runs on a **2-hour cycle** for passive miner income.

---

## Setup & Installation Instructions

### 1. Install Python & Dependencies
Ensure Python 3.10+ is installed, then run:
```bash
pip install -r requirements.txt
```

---

### 2. How to Get Your Session Token (`Cookie`)

1. Open Telegram Web in your browser (**`web.telegram.org/a/`** or **`web.telegram.org/k/`**).
2. Launch the **LyOS MiniApp**.
3. Press **`F12`** on your keyboard to open Developer Tools.
4. Click on the **Network** tab at the top of DevTools.
5. Perform an action inside LyOS (e.g., click the **SCAN** tab at the bottom right).
6. In the Network tab request list, click on any request (e.g. `system`, `scan`, `me`, or `active`).
7. On the right panel, open the **Headers** tab and scroll to **Request Headers**.
8. Copy the entire value string next to **`Cookie:`** (starts with `__Secure-next-auth.session-token=...`).

---

### 3. Add Token to `data.txt`
Paste the copied cookie string into `data.txt` (one account token per line):
```text
__Secure-next-auth.session-token=eyJhbGciOiJDIR...
```

---

### 4. Launch the Bot
Run the bot using:
```bash
python main.py
```

---

## Configuration (`config.json`)

Customize bot behavior, tap counts, and delay intervals in `config.json`:

```json
{
  "auto_tap": true,
  "auto_claim_farming": true,
  "auto_daily_checkin": true,
  "auto_complete_tasks": true,
  "tap_count_range": [30, 80],
  "delay_between_accounts_seconds": [3, 7],
  "loop_delay_minutes": [120, 120]
}
```
