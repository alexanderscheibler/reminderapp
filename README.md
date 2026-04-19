# reminderapp

# Reminder Agent — Vercel Deployment

## File structure
```
vercel_reminder/
├── vercel.json
├── requirements.txt
├── api/
│   └── reminder.py
└── public/
    └── index.html
```

## Deploy steps

### 1. Create a secret token
Pick any random string as your webhook secret, e.g.:
```
openssl rand -hex 16
```
Save it — you'll need it in two places.

### 2. Set environment variables in Vercel
In your Vercel project → Settings → Environment Variables, add:

| Name | Value |
|------|-------|
| `AZURE_ENDPOINT` | `https://localai121.services.ai.azure.com/openai/v1/` |
| `AZURE_API_KEY` | your key from the Azure deployment page |
| `GMAIL_ADDRESS` | the Gmail you send from |
| `GMAIL_APP_PW` | 16-character Gmail app password |
| `EMAIL_TO` | your@proton.me |
| `WEBHOOK_SECRET` | the random string you generated above |

### 3. Add the secret to index.html
In `public/index.html`, find this line:
```js
const WEBHOOK_SECRET = 'REPLACE_WITH_YOUR_SECRET';
```
Replace with the same random string.

### 4. Push to GitHub and connect to Vercel
```bash
git init
git add .
git commit -m "reminder agent"
# Create a repo on GitHub, then:
git remote add origin https://github.com/yourname/reminder-agent.git
git push -u origin main
```
Then in Vercel: New Project → Import from GitHub → select the repo → Deploy.

### 5. Done
Your app lives at `https://your-project.vercel.app`
Open it from your phone browser — it works anywhere.

---

## How security works

- **Azure API key**: only exists in Vercel env vars, never in code or git
- **Gmail app password**: same — Vercel env vars only
- **WEBHOOK_SECRET**: the frontend sends this header on every request;
  the function rejects anything without it — so random people can't hit
  your endpoint and burn your Azure credits
- **HTTPS**: Vercel provides this automatically on all deployments

## Gmail App Password setup
1. myaccount.google.com → Security
2. Enable 2-Step Verification
3. Search "App Passwords" → create one named "Reminder Agent"
4. Copy the 16-character password (spaces included are fine)