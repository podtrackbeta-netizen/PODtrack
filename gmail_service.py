"""
FieldPOD Gmail backend — the piece `mail.ts#searchPodEmails` /
`fetchTrackerText` and the standalone's "Pull inbox" call over HTTP.

It does three things:
  1. OAuth to Gmail (read-only) and cache the token.
  2. Search the mailbox for GE / Takkion tracker + POD emails.
  3. Download a chosen attachment and hand back CSV text (xlsx flattened by
     server/tracker.py) for the app's review-and-apply flow.

Run:  python server/gmail_service.py         (see server/README.md for setup)
"""
from __future__ import annotations

import base64
import os
import re
import tempfile
from email.utils import parsedate_to_datetime, parseaddr

from flask import Flask, jsonify, request
from flask_cors import CORS
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from tracker import attachment_to_text

# ─────────────────────────── config ───────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = os.environ.get("GMAIL_CREDENTIALS", os.path.join(HERE, "credentials.json"))
TOKEN_FILE = os.environ.get("GMAIL_TOKEN", os.path.join(HERE, "token.json"))
# Where a refreshed token gets cached when TOKEN_FILE itself isn't writable —
# e.g. a Render "Secret File" is mounted read-only. The refresh token doesn't
# change on refresh, so re-deriving a fresh access token here each time the
# instance restarts is harmless; it just means it isn't persisted long-term.
TOKEN_CACHE_FILE = os.path.join(tempfile.gettempdir(), "fieldpod_gmail_token_cache.json")
PORT = int(os.environ.get("PORT", "8000"))
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")
DEFAULT_QUERY = os.environ.get(
    "GMAIL_QUERY",
    '(subject:tracker OR subject:commissioning OR subject:POD OR '
    'subject:"plan of the day" OR subject:"daily report" OR "Limon III") '
    "newer_than:60d",
)

TRACKER_NAME = re.compile(r"(tracker|commissioning|progress|pod).*\.(csv|tsv|xlsx|xlsm)$", re.I)
TRACKER_MIME = re.compile(r"(spreadsheetml|ms-excel|csv|tab-separated)", re.I)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})


# ─────────────────────────── auth ───────────────────────────
def load_credentials() -> Credentials | None:
    # Prefer a previously-cached refresh from this instance's writable temp
    # dir (see _save) over the original — possibly read-only — TOKEN_FILE.
    source = TOKEN_CACHE_FILE if os.path.exists(TOKEN_CACHE_FILE) else TOKEN_FILE
    if not os.path.exists(source):
        return None
    creds = Credentials.from_authorized_user_file(source, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Google rejected the refresh token itself (expired — e.g. a
            # 7-day Testing-mode cap — or revoked). The stored token.json is
            # dead either way: treat it as "not connected" so callers fall
            # through to the interactive consent flow / a clean error,
            # instead of a 500 crashing every request.
            return None
        _save(creds)
    return creds if creds and creds.valid else None


def _save(creds: Credentials) -> None:
    try:
        with open(TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())
    except OSError:
        # TOKEN_FILE isn't writable (e.g. a Render Secret File, mounted
        # read-only) — cache the refreshed token in a writable temp file
        # instead, so this doesn't crash the process on every refresh.
        with open(TOKEN_CACHE_FILE, "w") as fh:
            fh.write(creds.to_json())


def run_consent_flow() -> Credentials:
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"{CREDENTIALS_FILE} not found — download an OAuth *Desktop* client "
            "from Google Cloud Console (see server/README.md)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save(creds)
    return creds


def gmail_client(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ─────────────────────────── message parsing ───────────────────────────
def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _walk_parts(part, out):
    out.append(part)
    for sub in part.get("parts", []) or []:
        _walk_parts(sub, out)


def _looks_like_tracker(name: str, mime: str) -> bool:
    name, mime = name or "", mime or ""
    return bool(TRACKER_NAME.search(name)) or (
        bool(TRACKER_MIME.search(mime)) and name.lower().endswith((".csv", ".tsv", ".xlsx", ".xlsm"))
    )


def _to_inbox_email(msg: dict) -> dict:
    payload = msg.get("payload", {}) or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", []) or []}
    parts: list = []
    _walk_parts(payload, parts)

    body_text = ""
    attachments = []
    for p in parts:
        filename = p.get("filename") or ""
        body = p.get("body", {}) or {}
        mime = p.get("mimeType", "") or ""
        if filename and body.get("attachmentId"):
            attachments.append(
                {
                    "id": body["attachmentId"],
                    "name": filename,
                    "mimeType": mime or "application/octet-stream",
                    "size": body.get("size"),
                    "isTracker": _looks_like_tracker(filename, mime),
                }
            )
        elif mime == "text/plain" and body.get("data") and not body_text:
            try:
                body_text = _b64(body["data"]).decode("utf-8", errors="replace")
            except Exception:
                pass

    name, addr = parseaddr(headers.get("from", ""))
    date_iso = ""
    if headers.get("date"):
        try:
            date_iso = parsedate_to_datetime(headers["date"]).date().isoformat()
        except Exception:
            date_iso = ""
    snippet = (msg.get("snippet", "") or body_text[:180]).strip()
    subject = (
        headers.get("subject")
        or headers.get("thread-topic")  # Outlook-origin mail
        or (snippet[:70] + "…" if snippet else "(no subject)")
    )

    return {
        "id": f"gmail:{msg['id']}",
        "messageId": msg["id"],
        "provider": "gmail",
        "from": addr or headers.get("from", ""),
        "fromName": name or (addr.split("@")[0] if addr else headers.get("from", "")),
        "subject": subject,
        "date": date_iso,
        "snippet": snippet,
        "body": body_text or snippet,
        "imported": False,
        "attachments": attachments or None,
    }


# ─────────────────────────── routes ───────────────────────────
@app.get("/api/health")
def health():
    return jsonify(ok=True, service="fieldpod-gmail", authed=load_credentials() is not None)


@app.get("/api/auth/status")
def auth_status():
    creds = load_credentials()
    if not creds:
        return jsonify(connected=False, email=None)
    try:
        prof = gmail_client(creds).users().getProfile(userId="me").execute()
        return jsonify(connected=True, email=prof.get("emailAddress"))
    except Exception as e:  # token revoked etc.
        return jsonify(connected=False, email=None, error=str(e))


@app.post("/api/auth/login")
def auth_login():
    """Opens a browser on THIS machine for Google consent, then caches the token."""
    try:
        creds = run_consent_flow()
        prof = gmail_client(creds).users().getProfile(userId="me").execute()
        return jsonify(connected=True, email=prof.get("emailAddress"))
    except Exception as e:
        return jsonify(connected=False, error=str(e)), 400


@app.post("/api/auth/logout")
def auth_logout():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return jsonify(connected=False)


@app.get("/api/inbox")
def inbox():
    creds = load_credentials()
    if not creds:
        return jsonify(ok=False, error="not_connected")
    query = request.args.get("query") or DEFAULT_QUERY
    try:
        limit = max(1, min(int(request.args.get("max", "25")), 50))
    except ValueError:
        limit = 25
    try:
        svc = gmail_client(creds)
        listing = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        ids = [m["id"] for m in listing.get("messages", [])]
        emails = []
        for mid in ids:
            msg = svc.users().messages().get(userId="me", id=mid, format="full").execute()
            emails.append(_to_inbox_email(msg))
        return jsonify(ok=True, provider="gmail", query=query, emails=emails)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502


@app.get("/api/attachment/<message_id>/<attachment_id>")
def attachment(message_id: str, attachment_id: str):
    creds = load_credentials()
    if not creds:
        return jsonify(ok=False, error="not_connected"), 401
    name = request.args.get("name", "attachment")
    try:
        svc = gmail_client(creds)
        att = (
            svc.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        raw = _b64(att["data"])
        text = attachment_to_text(name, raw)
        return jsonify(ok=True, name=name, bytes=len(raw), text=text)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 415
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502


if __name__ == "__main__":
    # 0.0.0.0 by default so this also works unmodified on a cloud host (Render,
    # Fly, etc.) where the platform routes external traffic to that interface;
    # override with HOST=127.0.0.1 for the old local-only behavior if you want it.
    host = os.environ.get("HOST", "0.0.0.0")
    authed = load_credentials() is not None
    print(f"FieldPOD Gmail service on http://{host}:{PORT}  (authenticated: {authed})")
    if not authed:
        print("  Not connected yet — POST /api/auth/login, or run:  python server/connect.py")
    app.run(host=host, port=PORT, threaded=True)
