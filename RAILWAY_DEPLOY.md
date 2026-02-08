# Railway Deployment Guide

## Quick Start

### 1. Prepare Your GitHub Repository

If you don't have a GitHub repo yet:

```bash
cd /Users/step/Desktop/new_bot
git config --global user.email "your.email@example.com"
git config --global user.name "Your Name"
git add .
git commit -m "Initial commit: receipt checking bot with SafeCheck API"
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/receipt-check-bot.git
git branch -M main
git push -u origin main
```

Replace `YOUR_GITHUB_USERNAME` with your actual GitHub username.

### 2. Create a Railway Project

1. Go to https://railway.app and sign up / log in
2. Click "New Project" button
3. Select "Deploy from GitHub repo"
4. Authorize Railway to access your GitHub
5. Select the `receipt-check-bot` repository
6. Click "Deploy"

Railway will automatically detect `Procfile` and `requirements.txt`.

### 3. Configure Environment Variables

In Railway dashboard:

1. Navigate to your project
2. Click "Variables" tab (or settings)
3. Add these variables:
   ```
   BOT_TOKEN=8487958575:AAHf5mzG0nwwVDRLac__jTcog-OO2G9v29E
   OWNER_TG_ID=6781252224
   SC_API_KEY=bb97014d466423ee30e48a83bbd670039c01c17e5f309503a449cb531e4e11ad
   SC_USER_ID=6781252224
   ```
4. Click "Save"

### 4. Verify Deployment

- Check the "Logs" tab in Railway
- You should see: `Bot started...`
- Railway will display a green checkmark when deployment is successful

### 5. Test the Bot

In Telegram, send `/start` to your bot. You should get a welcome message.

## Troubleshooting

**Bot won't start:**
- Check "Logs" tab for error messages
- Verify all env vars are set correctly
- Ensure `Procfile` has `worker: python main.py`

**Bot gets disconnected:**
- Railway free tier has limited uptime; consider upgrading to paid plan
- Check if API keys (Telegram, SafeCheck) are still valid

**Errors in logs:**
- `forbidden`: SafeCheck API key issue
- `Unauthorized`: Telegram bot token issue
- Other errors: check the full stack trace in Railway logs

## Manual Redeploy

After pushing changes to GitHub:

```bash
git add .
git commit -m "Your commit message"
git push origin main
```

Railway auto-redeployments trigger on each push (configurable in Railway settings).

## Useful Railway Commands

If using Railway CLI:

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link to project
railway link

# View logs
railway logs

# Deploy
railway up
```

## Cost

- **Free tier**: Limited monthly dyno hours (~725 hours/month = ~24 hours/day)
- **Paid plan**: $5/month for 1 dyno running 24/7
- SafeCheck API: Billed per check (see your account)

---

For more info, visit https://railway.app/docs
