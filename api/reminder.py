"""
api/reminder.py — Vercel serverless function
Receives reminder text → parses with Azure Phi-4-mini → sends .ics via Resend
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler

from openai import OpenAI
import resend

# ── Config (Vercel Environment Variables) ────────────────────────────────────
AZURE_ENDPOINT  = os.environ.get("AZURE_ENDPOINT", "")
AZURE_API_KEY   = os.environ.get("AZURE_API_KEY", "")
AZURE_MODEL     = "Phi-4-mini-instruct"

RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL      = os.environ.get("FROM_EMAIL", "onboarding@resend.dev") # Update when you add a custom domain to Resend
EMAIL_TO   = os.environ.get("EMAIL_TO", "")
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "")
ALLOWED_ORIGIN  = os.environ.get("ALLOWED_ORIGIN", "*") # e.g., https://your-app.vercel.app

DEFAULT_HOUR    = 19  # 7:00 PM
# ─────────────────────────────────────────────────────────────────────────────

ai_client = OpenAI(base_url=AZURE_ENDPOINT, api_key=AZURE_API_KEY)
resend.api_key = RESEND_API_KEY

def parse_reminder_with_ai(user_text: str, client_time_iso: str) -> dict:
    try:
        # Use the client's local time, not the server's UTC time
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
        return False, "I couldn't determine the date.", None

    try:
        event_date = datetime.strptime(parsed["date"], "%Y-%m-%d").date()
        client_date = datetime.fromisoformat(client_time_iso.replace('Z', '+00:00')).date()
    except Exception:
        return False, "The date format was invalid.", None

    if event_date < client_date:
        return False, "The date parsed is in the past.", None

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
        "datetime": event_dt.strftime("%Y-%m-%dT%H:%M:%S"), # ISO string for JSON passing
        "notes": parsed.get("notes") or ""
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


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Webhook-Secret")
        self.end_headers()

    def do_POST(self):
        # 1. CORS Validation
        origin = self.headers.get("Origin", "")
        if ALLOWED_ORIGIN != "*" and ALLOWED_ORIGIN != origin:
            self.send_error_response(403, "Forbidden Origin")
            return

        # 2. Secret Validation
        token = self.headers.get("X-Webhook-Secret", "")
        if token != WEBHOOK_SECRET:
            self.send_error_response(401, "Unauthorized: Invalid Secret")
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            action = body.get("action")
        except Exception:
            self.send_error_response(400, "Invalid JSON payload")
            return

        # ── ACTION: PARSE (Human-in-the-loop Step 1) ──
        if action == "parse":
            user_text = body.get("text", "").strip()
            client_time = body.get("clientTime")
            if not user_text or not client_time:
                self.send_error_response(400, "Missing text or clientTime")
                return

            try:
                parsed = parse_reminder_with_ai(user_text, client_time)
                is_valid, error_msg, event = validate_event(parsed, client_time)
                if not is_valid:
                    self.send_error_response(422, error_msg)
                    return
                self.send_json_response(200, {"success": True, "event": event})
            except Exception as e:
                self.send_error_response(500, f"AI parsing failed: {e}")

        # ── ACTION: SEND (Human-in-the-loop Step 2) ──
        elif action == "send":
            event = body.get("event")
            if not event:
                self.send_error_response(400, "Missing event data")
                return

            try:
                ics_content = build_ics(event)
                event_dt = datetime.fromisoformat(event["datetime"])
                date_str = event_dt.strftime("%A, %B %d at %I:%M %p")

                # Resend API Call
                resend.Emails.send({
                    "from": FROM_EMAIL,
                    "to": EMAIL_TO,
                    "subject": f"📅 Reminder: {event['title']}",
                    "text": f"Your reminder has been scheduled.\n\nEvent: {event['title']}\nWhen: {date_str}\nNotes: {event['notes']}",
                    "attachments": [
                        {
                            "filename": "reminder.ics",
                            "content": ics_content
                        }
                    ]
                })
                self.send_json_response(200, {"success": True})
            except Exception as e:
                self.send_error_response(500, f"Resend API error: {e}")
        else:
            self.send_error_response(400, "Invalid action")

    def send_json_response(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.end_headers()
        self.wfile.write(body)

    def send_error_response(self, status: int, message: str):
        self.send_json_response(status, {"error": message})

    def log_message(self, format, *args):
        pass