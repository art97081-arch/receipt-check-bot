# Receipt-check Bot (DataGrab integration)

This Telegram bot accepts PDF receipts and checks them using the DataGrab API.

Features
- Owner (by Telegram ID) can add allowed users with `/allow <tg_id>` and remove with `/disallow <tg_id>`.
- Allowed users can upload PDF receipts (as file) to the bot; the bot sends them to DataGrab and returns the JSON result.

## Local Setup

1. Copy `.env.example` to `.env` and fill values:

```
BOT_TOKEN=your_telegram_bot_token
OWNER_TG_ID=your_telegram_id
DATAGRAB_KEY=your_datagrab_api_key
```

2. Create and activate a Python environment (macOS zsh):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Run the bot:

```bash
python main.py
```

## Railway Deployment

### Prerequisites
- GitHub account
- Railway account (https://railway.app)

### Steps

1. **Push to GitHub:**
   ```bash
   git add .
   git commit -m "Initial commit: receipt checking bot"
   git remote add origin https://github.com/YOUR_USERNAME/receipt-check-bot.git
   git branch -M main
   git push -u origin main
   ```

2. **Deploy on Railway:**
   - Go to https://railway.app/dashboard
   - Click "New Project"
   - Select "Deploy from GitHub"
   - Authorize and choose your `receipt-check-bot` repository
   - Click "Deploy"

3. **Add Environment Variables:**
   - In Railway dashboard, go to your project
   - Click "Variables" tab
   - Add:
     - `BOT_TOKEN` = your bot token
     - `OWNER_TG_ID` = your TG ID
     - `DATAGRAB_KEY` = your DataGrab API key
   - Click "Save"

4. **Start the bot:**
   - Railway will auto-deploy when you push to GitHub
   - Check "Logs" tab to verify the bot started (`Bot started...`)

## Usage

- Owner runs `/allow 123456` to grant access to a user.
- Owner runs `/disallow 123456` to revoke access.
- Owner runs `/list` to see all allowed users.
- Allowed user sends a PDF (as document). The bot posts it to DataGrab and replies with the parsed result.

## Notes
- Do NOT commit your `.env` with real tokens.
- DataGrab request timeout: ~60 seconds per check.
- Railway free tier has monthly dyno hours; use a paid plan for 24/7 uptime.
