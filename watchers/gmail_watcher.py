"""Gmail Watcher — polls Gmail API for unread+important emails and writes
structured Markdown action files to vault/Needs_Action/.

Setup (one-time):
    1. Go to https://console.cloud.google.com and create a project.
    2. Enable "Gmail API" for that project.
    3. Create OAuth 2.0 credentials (Desktop application type).
    4. Download the credentials JSON and note its path.
    5. Set GMAIL_CREDENTIALS=/path/to/credentials.json in your .env file.
    6. First run opens a browser for the OAuth consent screen.
       The resulting token is saved to watchers/.state/gmail_token.json
       and reused on subsequent runs.

Usage:
    python gmail_watcher.py <vault_path>

Example:
    python gmail_watcher.py ../vault

PM2 (keep alive):
    pm2 start gmail_watcher.py --interpreter python3 -- ../vault
"""

import os
import sys
import argparse
import email.utils
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Google API client imports
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError as exc:
    sys.exit(
        f"Missing dependency: {exc}\n"
        "Run: pip install google-auth google-auth-oauthlib google-auth-httplib2 "
        "google-api-python-client"
    )

from base_watcher import BaseWatcher, with_retry

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Read + send scopes — shared token used by both watcher and gmail-sender skill
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Stored alongside other watcher state
_TOKEN_FILE = Path(__file__).parent / ".state" / "gmail_token.json"

# Maximum emails fetched per poll (avoids large bursts on first run)
_MAX_RESULTS = 30


# ---------------------------------------------------------------------------
# Sender classification
# ---------------------------------------------------------------------------

_BOT_KEYWORDS = ("noreply", "no-reply", "notifications@", "donotreply", "mailer-daemon")
_BOT_DOMAINS  = ("github.com", "gitlab.com", "bitbucket.org", "jira.com", "linear.app",
                  "asana.com", "trello.com", "slack.com", "notion.so")


def _sender_tag(sender: str) -> str:
    """Return 'BOT_<name>' or 'HUMAN_<name>' based on the sender string."""
    lower = sender.lower()
    is_bot = (
        "[bot]" in lower
        or any(kw in lower for kw in _BOT_KEYWORDS)
        or any(lower.endswith(f"@{d}>") or f"@{d}" in lower for d in _BOT_DOMAINS)
    )
    # Extract a short display name (before the email address)
    import re
    name_match = re.match(r'^"?([^"<]+)"?\s*(?:<.*>)?$', sender.strip())
    name = name_match.group(1).strip() if name_match else sender.split("@")[0]
    # Sanitise to filename-safe characters, max 20 chars
    safe = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')[:20]
    prefix = "BOT" if is_bot else "HUMAN"
    return f"{prefix}_{safe}"


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------


def _get_credentials(credentials_path: Path) -> Credentials:
    """Return valid OAuth2 Credentials, refreshing or re-authenticating as needed."""
    creds: Credentials | None = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), SCOPES
            )
            creds = flow.run_local_server(port=0)

        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return creds


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class GmailWatcher(BaseWatcher):
    """Polls Gmail for unread+important emails and creates Needs_Action entries.

    Each new email produces a file at:
        vault/Needs_Action/EMAIL_<gmail_message_id>.md
    """

    def __init__(self, vault_path: str, credentials_path: str):
        super().__init__(vault_path, check_interval=30)  # poll every 30 seconds

        creds_path = Path(credentials_path).expanduser().resolve()
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Gmail credentials file not found: {creds_path}\n"
                "Download it from Google Cloud Console → APIs & Services → Credentials."
            )

        self.logger.info("Authenticating with Gmail API…")
        creds = _get_credentials(creds_path)
        self._service = build("gmail", "v1", credentials=creds)
        self.logger.info("Gmail API ready.")

    # ------------------------------------------------------------------
    # BaseWatcher interface
    # ------------------------------------------------------------------

    @with_retry(max_attempts=3, base_delay=10.0)
    def check_for_updates(self) -> list:
        """Fetch unread+important message stubs not yet processed."""
        try:
            result = (
                self._service.users()
                .messages()
                .list(
                    userId="me",
                    q="is:unread is:important",
                    maxResults=_MAX_RESULTS,
                )
                .execute()
            )
        except HttpError as exc:
            self.logger.error(f"Gmail API list error: {exc}")
            return []

        messages = result.get("messages", [])
        new = [m for m in messages if not self._is_processed(m["id"])]
        self.logger.debug(f"Gmail: {len(messages)} unread+important, {len(new)} new.")
        return new

    @with_retry(max_attempts=3, base_delay=10.0)
    def create_action_file(self, message: dict) -> Path:
        """Fetch full message metadata and write EMAIL_<id>.md."""
        try:
            msg = (
                self._service.users()
                .messages()
                .get(
                    userId="me",
                    id=message["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
        except HttpError as exc:
            self.logger.error(f"Gmail API get error for {message['id']}: {exc}")
            raise

        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }

        sender  = headers.get("From", "Unknown")
        to      = headers.get("To", "")
        subject = headers.get("Subject", "(no subject)")
        date_raw = headers.get("Date", "")
        snippet  = msg.get("snippet", "")

        # Normalise received timestamp
        try:
            parsed_dt = email.utils.parsedate_to_datetime(date_raw)
            received = parsed_dt.astimezone(timezone.utc).isoformat()
        except Exception:
            received = datetime.now(timezone.utc).isoformat()

        def _ys(value: str) -> str:
            """Wrap a value in single-quoted YAML string, escaping internal quotes."""
            return "'" + value.replace("'", "''") + "'"

        trace_id = str(uuid.uuid4())

        content = f"""\
---
type: email
source: gmail
from: {_ys(sender)}
to: {_ys(to)}
subject: {_ys(subject)}
received: {received}
priority: high
status: pending
trace_id: {trace_id}
---

## Email: {subject}

**From:** {sender}
**To:** {to}
**Date:** {date_raw}

### Preview

{snippet}

## Suggested Actions

- [ ] Review email and determine response
- [ ] Reply to sender if required
- [ ] Forward to the relevant party if needed
- [ ] Archive after processing
- [ ] Move this file to /Done when complete
"""

        tag = _sender_tag(sender)
        filepath = self.needs_action / f"EMAIL_{tag}_{message['id']}.md"
        filepath.write_text(content, encoding="utf-8")
        self._mark_processed(message["id"])
        return filepath


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Watch Gmail for unread+important emails "
            "and write structured Needs_Action files."
        )
    )
    parser.add_argument(
        "vault",
        help="Path to the Obsidian vault directory (e.g. ../vault)",
    )
    args = parser.parse_args()

    creds = os.getenv("GMAIL_CREDENTIALS")
    if not creds:
        print(
            "ERROR: GMAIL_CREDENTIALS is not set.\n"
            "Add it to your .env file: GMAIL_CREDENTIALS=/path/to/credentials.json",
            file=sys.stderr,
        )
        sys.exit(1)

    watcher = GmailWatcher(args.vault, creds)
    watcher.run()


if __name__ == "__main__":
    main()
