# Google Cloud Setup Guide
### Email Cleanup & Brief Manager

This guide walks you through the Google Cloud setup required to let the app read your Gmail and Google Calendar. You only need to do this once.

**Total time: about 10–15 minutes**

> **IMPORTANT — order matters.** Publish the OAuth app (Step 5) **before** authorizing any Gmail accounts (Step 8). Tokens generated while the app is still in "Testing" mode keep a 7-day expiry clock even after you later publish — so if you authorize first and publish later, you'll be re-doing the consent flow in a week. Follow the steps in order and you avoid this entirely.

---

## Step 1 — Create a Google Cloud Project

1. Go to **https://console.cloud.google.com**
2. Sign in with your Google account (the main one you want to track)
3. At the top of the page, click the project dropdown (it may say "Select a project" or show an existing project name)
4. Click **New Project**
5. Name it anything — e.g. `Personal Tracker`
6. Click **Create**
7. Wait a few seconds, then make sure your new project is selected in the dropdown at the top

---

## Step 2 — Enable the Gmail API

1. In the left sidebar, click **APIs & Services** → **Library**
2. In the search box, type **Gmail API**
3. Click on **Gmail API** in the results
4. Click the blue **Enable** button
5. Wait for it to enable (10–15 seconds)

---

## Step 3 — Enable the Google Calendar API

1. Click the back arrow or go to **APIs & Services** → **Library** again
2. Search for **Google Calendar API**
3. Click on **Google Calendar API**
4. Click **Enable**

---

## Step 4 — Configure the OAuth Consent Screen

1. In the left sidebar, go to **Google Auth Platform** → **Branding** (Google renamed "OAuth consent screen" to this in late 2025)
2. If prompted, select **External** and click **Create**
3. Fill in the required fields:
   - **App name:** Personal Tracker (or anything you like)
   - **User support email:** your Gmail address
   - **Developer contact email:** your Gmail address
4. Click **Save**
5. You can skip adding Test Users — Step 5 makes them unnecessary

---

## Step 5 — Publish the OAuth App (do this BEFORE generating tokens)

By default the app is in **Testing** mode, which gives every refresh token a 7-day kill switch. Publish first, then generate tokens — that way every token issued is a long-lived Production token from day one.

1. In the left sidebar under **Google Auth Platform**, click **Audience**
2. You'll see **Publishing status: Testing**
3. Click the **PUBLISH APP** button
4. Confirm the dialog
5. Status changes to **In production**

**No Google review or verification is needed** for a personal-use app. The "Google hasn't verified this app" warning will still appear on first authorization (just click **Continue** → **Advanced** → **Go to [app] (unsafe)**) — but tokens issued from now on will live until you manually revoke them.

> If you already authorized accounts before publishing, those tokens are stuck on the 7-day clock. After publishing, move all existing `credentials/token_*.json` files into a dated backup folder (e.g. `credentials/_expired_backup_YYYY-MM-DD/`) and re-run `python main.py setup-accounts` to mint fresh Production tokens.

---

## Step 6 — Create OAuth Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **+ Create Credentials** at the top
3. Choose **OAuth client ID**
4. For **Application type**, select **Desktop app**
5. Name it anything — e.g. `Tracker Desktop`
6. Click **Create**
7. A popup will appear — click **Download JSON**
8. This downloads a file with a long name like `client_secret_123456...json`

---

## Step 7 — Place the Credentials File

1. Rename the downloaded file to exactly: **`credentials.json`**
2. Move it into the `credentials` folder inside the app folder:
   ```
   briefing-bot/
     credentials/
       credentials.json   ← put it here
   ```

---

## Step 8 — Authorize Your Gmail Accounts

Now return to the setup wizard (or run `python main.py setup-accounts`). It will open a browser tab for each Gmail account in your config. Sign in with the matching account each time and click **Allow**.

Because the app is already in Production (from Step 5), every token issued here is long-lived.

---

## Troubleshooting

**"Access blocked" or "App not verified" when authorizing Gmail**
→ Expected on the first sign-in to a Production app. Click **Continue** → **Advanced** → **Go to [app name] (unsafe)** → **Allow**. The "unsafe" wording is misleading — it just means the app isn't Google-verified, which isn't required for personal use.

**Bot stopped working after about a week**
→ Tokens were generated while the app was in Testing. Verify the app is "In production" on the Audience page, then move existing `credentials/token_*.json` files into `credentials/_expired_backup_YYYY-MM-DD/` and re-run `python main.py setup-accounts`.

**"APIs not enabled" error**
→ Double-check Steps 2 and 3 — make sure both Gmail API and Calendar API show as "Enabled"

**Can't find the credentials folder**
→ It's inside the briefing-bot app folder, same place as this guide

**Still stuck?**
→ Email the person who shared this app with you and include a screenshot of the error
