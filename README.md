# Reminder Agent v3

## What was fixed

**Vercel build error:** `api/**/*` is not a valid Vercel function pattern.
Functions must be listed explicitly by filename:
```json
"api/login.py":    { "runtime": "python3.12" },
"api/reminder.py": { "runtime": "python3.12" }
```

**Security:** Removed `localStorage` secret (visible in DevTools → Application).
Now uses an HttpOnly session cookie set server-side — JS cannot read it at all.

---

## File structure
```
├── vercel.json
├── requirements.txt
├── api/
│   ├── login.py       ← issues signed HttpOnly cookie on correct password
│   └── reminder.py    ← validates cookie, calls Azure + Resend
└── public/
    ├── login.html     ← password form
    └── app.html       ← main UI (no secrets anywhere in JS)
```

## Vercel environment variables
| Variable | Value |
|----------|-------|
| `AZURE_ENDPOINT` | `https://localai121.services.ai.azure.com/openai/v1/` |
| `AZURE_API_KEY` | from Azure deployment page |
| `RESEND_API_KEY` | from resend.com dashboard |
| `FROM_EMAIL` | verified sender in Resend |
| `EMAIL_TO` | your@proton.me |
| `APP_PASSWORD` | password you type to log in |
| `SESSION_SECRET` | run: `openssl rand -hex 32` |

## Deploy
```bash
git add .
git commit -m "v3 - fixed vercel config + secure session auth"
git push
```
Vercel auto-deploys on push.