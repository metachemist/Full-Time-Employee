#!/usr/bin/env python3
"""
Gmail Sender — AI Employee Silver Tier skill script.

Sends a single email via Gmail API using the existing OAuth token from the
Gmail watcher setup. Designed to be called after human approval.

Usage:
    python send_email.py --to "Name <email>" --subject "..." --body "..."
    python send_email.py --to "..." --subject "..." --body-file /tmp/body.txt
    python send_email.py --to "..." --subject "..." --body "..." --dry-run
    python send_email.py --list-sent --limit 5
"""

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency guard
# ---------------------------------------------------------------------------
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    sys.exit(
        "Missing dependency. Run:\n"
        "  pip install google-auth google-auth-oauthlib google-api-python-client"
    )

# ---------------------------------------------------------------------------
# Defaults (resolved relative to this script's project root)
# ---------------------------------------------------------------------------
_SCRIPT_DIR  = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent.parent.parent.parent  # .claude/skills/gmail-sender/scripts → project root
_DEFAULT_TOKEN       = _PROJECT_DIR / "watchers" / ".state" / "gmail_token.json"
_DEFAULT_CREDENTIALS = _PROJECT_DIR / "watchers" / ".secrets" / "gmail_credentials.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.readonly"]


def _load_credentials(token_path: Path, creds_path: Path) -> Credentials:
    if not token_path.exists():
        raise FileNotFoundError(
            f"Gmail token not found: {token_path}\n"
            "Run the Gmail watcher once to complete OAuth flow."
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds.expired and creds.refresh_token:
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Gmail credentials not found: {creds_path}\n"
                "Set GMAIL_CREDENTIALS in your .env file."
            )
        import google.auth.transport.requests as _rq
        creds.refresh(_rq.Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")

    if not creds.valid:
        raise RuntimeError("Gmail credentials are invalid or expired. Re-run auth flow.")

    return creds


def _build_message(to: str, subject: str, body: str, reply_to: str | None = None) -> dict:
    msg = MIMEMultipart("alternative")
    msg["To"]      = to
    msg["Subject"] = subject
    if reply_to:
        msg["In-Reply-To"] = reply_to
        msg["References"]  = reply_to

    msg.attach(MIMEText(body, "plain", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}


def send_email(
    to: str,
    subject: str,
    body: str,
    token_path: Path,
    creds_path: Path,
    reply_to: str | None = None,
    dry_run: bool = False,
) -> dict:
    ts = datetime.now(timezone.utc).isoformat()

    if dry_run:
        return {
            "status":       "dry_run",
            "would_send_to": to,
            "subject":       subject,
            "body_preview":  body[:120] + ("..." if len(body) > 120 else ""),
            "timestamp":     ts,
        }

    creds   = _load_credentials(token_path, creds_path)
    service = build("gmail", "v1", credentials=creds)
    message = _build_message(to, subject, body, reply_to)

    sent = service.users().messages().send(userId="me", body=message).execute()
    return {
        "status":     "sent",
        "message_id": sent.get("id"),
        "to":         to,
        "subject":    subject,
        "timestamp":  ts,
    }


def list_sent(token_path: Path, creds_path: Path, limit: int = 5) -> list[dict]:
    creds   = _load_credentials(token_path, creds_path)
    service = build("gmail", "v1", credentials=creds)
    results = service.users().messages().list(
        userId="me", labelIds=["SENT"], maxResults=limit
    ).execute()
    messages = []
    for m in results.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["To", "Subject", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        messages.append({
            "id":      m["id"],
            "to":      headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date":    headers.get("Date", ""),
        })
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gmail Sender — AI Employee Silver Tier action script."
    )
    parser.add_argument("--to",          help="Recipient (Name <email> or plain email)")
    parser.add_argument("--subject",     help="Email subject line")
    parser.add_argument("--body",        help="Email body text (use --body-file for long emails)")
    parser.add_argument("--body-file",   help="Path to file containing the email body")
    parser.add_argument("--reply-to",    help="Message-ID of email being replied to (optional)")
    parser.add_argument("--token",       default=str(_DEFAULT_TOKEN),
                        help=f"Path to gmail_token.json (default: {_DEFAULT_TOKEN})")
    parser.add_argument("--credentials", default=str(_DEFAULT_CREDENTIALS),
                        help=f"Path to gmail_credentials.json (default: {_DEFAULT_CREDENTIALS})")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Log intended action without sending")
    parser.add_argument("--list-sent",   action="store_true",
                        help="List recent sent emails and exit")
    parser.add_argument("--limit",       type=int, default=5,
                        help="Number of sent emails to list (default: 5)")
    args = parser.parse_args()

    token_path = Path(args.token).expanduser().resolve()
    creds_path = Path(args.credentials).expanduser().resolve()

    if args.list_sent:
        try:
            sent = list_sent(token_path, creds_path, args.limit)
            print(json.dumps(sent, indent=2))
        except Exception as exc:
            print(json.dumps({"status": "error", "error": str(exc)}))
            sys.exit(1)
        return

    # ── Validate required args ────────────────────────────────────────────
    if not args.to:
        parser.error("--to is required for sending")
    if not args.subject:
        parser.error("--subject is required for sending")

    body = args.body or ""
    if args.body_file:
        bf = Path(args.body_file).expanduser()
        if not bf.exists():
            print(json.dumps({"status": "error", "error": f"body-file not found: {bf}"}))
            sys.exit(1)
        body = bf.read_text(encoding="utf-8")

    if not body.strip():
        parser.error("Email body is empty. Provide --body or --body-file.")

    try:
        result = send_email(
            to          = args.to,
            subject     = args.subject,
            body        = body,
            token_path  = token_path,
            creds_path  = creds_path,
            reply_to    = args.reply_to,
            dry_run     = args.dry_run,
        )
        print(json.dumps(result))
        sys.exit(0 if result["status"] in ("sent", "dry_run") else 1)

    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "error": str(exc),
                          "timestamp": datetime.now(timezone.utc).isoformat()}))
        sys.exit(1)
    except HttpError as exc:
        print(json.dumps({"status": "error", "error": f"Gmail API error: {exc}",
                          "timestamp": datetime.now(timezone.utc).isoformat()}))
        sys.exit(1)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc),
                          "timestamp": datetime.now(timezone.utc).isoformat()}))
        sys.exit(1)


if __name__ == "__main__":
    main()
