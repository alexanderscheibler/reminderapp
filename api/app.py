"""
api/reminder.py — Vercel serverless function
Parse → validate → confirm → send .ics via Resend.
Auth: HttpOnly session cookie (never exposed to browser JS).
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler

import resend
from openai import OpenAI

# ── Env vars (Vercel dashboard only — never in code) ─────────────────────────
AZURE_ENDPOINT  = os.environ.get("AZURE_ENDPOINT", "")
AZURE_API_KEY   = os.environ.get("AZURE_API_KEY", "")
AZURE_MODEL     = "Phi-4-mini-instruct"

RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL      = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
EMAIL_TO        = os.environ.get("EMAIL_TO", "")

SESSION_SECRET  = os.environ.get("SESSION_SECRET", "")  # same value as login.py
SESSION_TTL     = 60 * 60 * 12

DEFAULT_HOUR    = 19  # 7:00 PM fallback
# ─────────────────────────────────────────────────────────────────────────────

ai_client = OpenAI(base_url=AZURE_ENDPOINT, api_key=AZURE_API_KEY)
resend.api_key = RESEND_API_KEY


# ── Session validation ───────────────────────────────────────────────────────

def get_session_token(cookie_header: str) -> str | None:
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if part.startswith("session="):
            return part[len("session="):]
    return None


def verify_token(token: str) -> bool:
    try:
        ts_str, sig = token.split(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), ts_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        if time.time() - int(ts_str) > SESSION_TTL:
            return False
        return True
    except Exception:
        return False


# ── AI + event logic (your code, unchanged) ──────────────────────────────────

def parse_reminder_with_ai(user_text: str, client_time_iso: str) -> dict:
    try:
        client_date = datetime.fromisoformat(client_time_iso.replace('Z', '+00:00'))
    except Exception:
        client_date = datetime.now()

    system_prompt = f"""You are a calendar assistant. The user's current local date/time is {client_date.strftime('%A, %B %d, %Y at %H:%M')}.

Extract reminder details from the user's message and return ONLY valid JSON with these fields:
- "title": short event title (string)
- "date": the event date in YYYY-MM-DD format (string)
- "time": the event time in HH:MM 24h format, or null if not specified
- "notes": any extra detail worth keeping, or null

Rules:
- "tomorrow" = {(client_date + timedelta(days=1)).strftime('%Y-%m-%d')}
- "next [weekday]" = the NEXT upcoming occurrence of that weekday after today
- "in X days" = today + X days
- If no time is mentioned, return null for time
- If no date is mentioned or it is ambiguous, return "date": null
- NEVER invent a date. If unsure, return null.
- Return ONLY the JSON object. No explanation. No markdown."""

    response = ai_client.chat.completions.create(
        model=AZURE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        max_tokens=200,
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def validate_event(parsed: dict, client_time_iso: str):
    if not parsed.get("title"):
        return False, "I couldn't extract a clear event title.", None
    if not parsed.get("date"):
        return False, "I couldn't determine the date. Try 'tomorrow', 'next Monday', or 'April 25'.", None

    try:
        event_date = datetime.strptime(parsed["date"], "%Y-%m-%d").date()
        client_date = datetime.fromisoformat(client_time_iso.replace('Z', '+00:00')).date()
    except Exception:
        return False, "The date format was invalid.", None

    if event_date < client_date:
        return False, f"{parsed['date']} is in the past. Did you mean a future date?", None

    if event_date > (client_date + timedelta(days=365)):
        return False, f"{parsed['date']} is more than a year away — that seems off.", None

    if parsed.get("time"):
        try:
            hour, minute = map(int, parsed["time"].split(":"))
        except Exception:
            hour, minute = DEFAULT_HOUR, 0
    else:
        hour, minute = DEFAULT_HOUR, 0

    event_dt = datetime(event_date.year, event_date.month, event_date.day, hour, minute)
    return True, "", {
        "title": parsed["title"].strip(),
        "datetime": event_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": parsed.get("notes") or "",
    }


def build_ics(event: dict) -> str:
    uid = str(uuid.uuid4())
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    event_dt = datetime.fromisoformat(event["datetime"])
    start = event_dt.strftime("%Y%m%dT%H%M%S")
    end = (event_dt + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//ReminderAgent//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now}
DTSTART:{start}
DTEND:{end}
SUMMARY:{event['title']}
DESCRIPTION:{event['notes']}
BEGIN:VALARM
TRIGGER:-PT30M
ACTION:DISPLAY
DESCRIPTION:Reminder: {event['title']}
END:VALARM
END:VEVENT
END:VCALENDAR"""


# ── Vercel handler ───────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        def do_GET(self):
            # ── Auth Check for the Frontend ──────────────────────────────────────
            token = get_session_token(self.headers.get("Cookie", ""))
            if not token or not verify_token(token):
                # 401 → UI will catch this and kick them to /login
                self.send_json(401, {"error": "Not authenticated"})
                return

            # 200 → Token is valid, UI is allowed to load
            self.send_json(200, {"status": "Authenticated"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "same-origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        # ── Auth: validate session cookie ────────────────────────────────────
        # The cookie was set by /api/login as HttpOnly.
        # Browser sends it automatically — JS never sees it, can't steal it.
        token = get_session_token(self.headers.get("Cookie", ""))
        if not token or not verify_token(token):
            # 401 → frontend redirects to /login
            self.send_json(401, {"error": "Not authenticated"})
            return

        # ── Parse body ───────────────────────────────────────────────────────
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self.send_json(400, {"error": "Invalid JSON"})
            return

        action = body.get("action")

        # ── ACTION: parse ────────────────────────────────────────────────────
        if action == "parse":
            user_text   = (body.get("text") or "").strip()
            client_time = body.get("clientTime", "")
            if not user_text or not client_time:
                self.send_json(400, {"error": "Missing text or clientTime"})
                return
            try:
                parsed = parse_reminder_with_ai(user_text, client_time)
                is_valid, error_msg, event = validate_event(parsed, client_time)
                if not is_valid:
                    self.send_json(422, {"error": error_msg})
                    return
                self.send_json(200, {"success": True, "event": event})
            except json.JSONDecodeError:
                self.send_json(422, {"error": "AI returned unexpected output. Try rephrasing."})
            except Exception as e:
                self.send_json(500, {"error": f"AI error: {e}"})

        # ── ACTION: send ─────────────────────────────────────────────────────
        elif action == "send":
            event = body.get("event")
            if not event:
                self.send_json(400, {"error": "Missing event data"})
                return
            try:
                ics_content = build_ics(event)
                event_dt = datetime.fromisoformat(event["datetime"])
                date_str = event_dt.strftime("%A, %B %d at %I:%M %p")

                resend.Emails.send({
                    "from": FROM_EMAIL,
                    "to": EMAIL_TO,
                    "subject": f"📅 Reminder: {event['title']}",
                    "text": (
                        f"Your reminder has been scheduled.\n\n"
                        f"Event: {event['title']}\n"
                        f"When:  {date_str}\n"
                        + (f"Notes: {event['notes']}\n" if event['notes'] else "")
                        + "\nOpen the attached .ics file to add it to your Proton Calendar."
                    ),
                    "attachments": [{
                        "filename": "reminder.ics",
                        "content": ics_content,
                    }],
                })
                self.send_json(200, {"success": True})
            except Exception as e:
                self.send_json(500, {"error": f"Resend error: {e}"})

        else:
            self.send_json(400, {"error": "Invalid action"})

    def send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass