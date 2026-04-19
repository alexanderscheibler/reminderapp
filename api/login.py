"""
api/login.py — Password check. Issues a signed HttpOnly session cookie.
Nothing secret is ever sent to the browser.
"""

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler

APP_PASSWORD   = os.environ.get("APP_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_TTL    = 60 * 60 * 12  # 12 hours


def make_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(SESSION_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


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


def json_resp(h, status: int, data: dict, extra_headers=None):
    body = json.dumps(data).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    for name, value in (extra_headers or []):
        h.send_header(name, value)
    h.end_headers()
    h.wfile.write(body)


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "same-origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            json_resp(self, 400, {"error": "Invalid request"})
            return

        password = (body.get("password") or "").strip()

        # Constant-time comparison prevents timing attacks
        if not APP_PASSWORD or not hmac.compare_digest(password, APP_PASSWORD):
            json_resp(self, 401, {"error": "Invalid password"})
            return

        token = make_token()
        cookie = (
            f"session={token}; HttpOnly; Secure; SameSite=Strict; "
            f"Path=/; Max-Age={SESSION_TTL}"
        )
        json_resp(self, 200, {"ok": True}, extra_headers=[("Set-Cookie", cookie)])

    def log_message(self, *a):
        pass