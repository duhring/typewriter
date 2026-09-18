# Google Calendar Setup for PKA

One-time setup to connect your Google Calendar. Takes about 5 minutes.

## Step 1: Create a Google Cloud Project

1. Go to https://console.cloud.google.com/
2. Click the project dropdown at the top → **New Project**
3. Name it something like "PKA Calendar" → **Create**
4. Make sure the new project is selected

## Step 2: Enable the Google Calendar API

1. Go to **APIs & Services → Library** (left sidebar)
2. Search for "Google Calendar API"
3. Click it → **Enable**

## Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** → **Create**
3. Fill in:
   - App name: `PKA Calendar`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue**
5. On the Scopes page, click **Add or Remove Scopes**
   - Search for `Google Calendar API` and check `.../auth/calendar`
   - Click **Update** → **Save and Continue**
6. On the Test Users page, click **Add Users**
   - Add your Google email address
   - Click **Save and Continue**
7. Click **Back to Dashboard**

## Step 4: Create OAuth Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth Client ID**
3. Application type: **Desktop app**
4. Name: `PKA` (or anything)
5. Click **Create**
6. Click **Download JSON** on the popup

## Step 5: Install the Credentials

Save the downloaded JSON file as:
```
PKA/data/gcal/client_secret.json
```

## Step 6: Authenticate

Run this command from the PKA folder:
```bash
discord-bridge/venv/bin/python3 tools/gcal.py auth
```

A browser window will open. Sign in with your Google account and grant calendar access. You'll see a success message in the terminal.

## Step 7: Test

```bash
discord-bridge/venv/bin/python3 tools/gcal.py list
```

You should see today's calendar events as JSON.

## Done!

Larry can now manage your calendar from both the terminal and Discord. Try sending "What's on my calendar today?" in your `#larry` Discord channel.

## Notes

- Your credentials are stored in `data/gcal/` (ignored by git)
- The token auto-refreshes, but if you're in "Testing" mode on Google Cloud, it expires every 7 days. Just run `tools/gcal.py auth` again if that happens.
- To avoid the 7-day expiry: go to Google Cloud Console → OAuth consent screen → **Publish App**
