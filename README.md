# Slipt Bot v6 — Changelog & Setup Guide

## ✅ What's New in v6

### Feature 1 — Owner Fixed API (Simplified Login)
Users no longer need to enter API ID or API Hash.
The bot owner adds **one set of credentials** in `.env` and all user logins use them automatically.

**User login flow is now:**
1. Tap ➕ Login → Add Account
2. Send phone number
3. Enter OTP via PIN pad
4. Enter 2FA password (if enabled) — shown automatically

**Owner setup:**
```
OWNER_API_ID=12345678
OWNER_API_HASH=abcdef1234567890abcdef1234567890
```
Get these from https://my.telegram.org

---

### Feature 2 — Ad Templates (1-Click Campaign Reuse)
After launching any ad campaign you'll see a **💾 Save as Template** button.
Give it a name (e.g. "Daily Ad") and it's saved forever.

Next time — go to **📋 Templates** from the main menu → tap template → see a summary → go to **📤 Start Ad**.

Template stores:
- Ad mode (Custom / Saved / Link)
- Group mode (All / Selected)
- Group delay & batch delay
- Account phone label

Templates can be deleted with the 🗑 button next to each one.

---

### Feature 3 — Profile Update (Name / Bio / Photo)
Go to **👤 Profile** → select account → tap **👤 Update Profile**.

Three buttons:
- ✏️ **Change Name** — Send new display name (first + last, split on first space)
- 📝 **Change Bio** — Send new bio (max 70 chars)
- 🖼 **Change Photo** — Send a photo — uploaded directly to Telegram via Telethon

All updates happen via Telethon on the selected account's session — no extra API calls.

---

## 🚀 VPS Setup (Ubuntu)

### 1. Clone and install
```bash
git clone <your-repo> slipt_bot
cd slipt_bot
pip install -r requirements.txt
```

### 2. Configure .env
```bash
cp .env.example .env
nano .env
```
Fill in all values — especially `OWNER_API_ID` and `OWNER_API_HASH`.

### 3. Run with PM2 (recommended)
```bash
npm install -g pm2
mkdir -p logs
pm2 start ecosystem.config.js
pm2 save
pm2 startup   # run the printed command as root to enable auto-start
```

### 4. Run with systemd (alternative)
```bash
sudo cp slipt-bot.service /etc/systemd/system/
# Edit WorkingDirectory and User in the service file first!
sudo systemctl daemon-reload
sudo systemctl enable slipt-bot
sudo systemctl start slipt-bot
sudo systemctl status slipt-bot
```

### 5. Logs
- PM2: `pm2 logs slipt-bot`
- systemd: `journalctl -u slipt-bot -f`
- File: `tail -f slipt_bot.log`

---

## 📁 Project Structure
```
slipt_bot/
├── main.py                  # Launcher — runs both bots
├── .env                     # Your config (never commit this)
├── .env.example             # Template
├── ecosystem.config.js      # PM2 config
├── slipt-bot.service        # systemd config
├── requirements.txt
├── sessions/                # Telethon session files (auto-created)
├── user_bot/
│   ├── bot.py               # Main user-facing bot
│   ├── keyboards.py         # All inline keyboards
│   └── states.py            # ConversationHandler states
├── admin_bot/
│   └── bot.py               # Admin control panel
└── common/
    ├── database.py          # SQLAlchemy models + DBManager
    ├── ad_engine.py         # Ad sending logic
    └── session_manager.py   # Telethon login/session management
```
