"""WhatsApp Web Watcher — uses Playwright to monitor WhatsApp Web for
messages containing priority keywords, writing structured Markdown action
files to vault/Needs_Action/.

Architecture:
    - Opens a persistent Chromium context so the QR-code login survives restarts.
    - Scans ALL visible chats (not just "unread" — WhatsApp hides that in CSS).
    - Keyword filter + SHA-256 state deduplication prevent duplicate action files.

Selectors confirmed against WhatsApp Web 2026-02-21:
    Row:     div[role='row']
    Sender:  div._ak8l span.x1iyjqo2[title]     (title attr = display name)
    Preview: div._ak8k span[title]               (title attr = full message text)

Setup (one-time):
    1. Install Playwright browsers:
           playwright install chromium
    2. Set WHATSAPP_SESSION_PATH in your .env file.
    3. First-run authentication — QR scan needed:
           WHATSAPP_HEADLESS=false python whatsapp_watcher.py ../vault
       Scan the QR code, wait for WhatsApp to fully load, then Ctrl+C.
    4. Subsequent runs work headless without QR code.

Usage:
    python whatsapp_watcher.py <vault_path>

PM2:
    pm2 start whatsapp_watcher.py --interpreter python3 -- ../vault
"""

import os
import re
import sys
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
except ImportError:
    sys.exit(
        "Missing dependency: playwright\n"
        "Run: pip install playwright && playwright install chromium"
    )

from base_watcher import BaseWatcher

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KEYWORDS: frozenset[str] = frozenset(
    {"urgent", "invoice", "payment", "order", "pricing", "quote", "asap", "help"}
)

# How many chat rows to scan per cycle
_MAX_CHATS = 30

# ---------------------------------------------------------------------------
# Selectors — confirmed against WhatsApp Web 2026-02-21
# ---------------------------------------------------------------------------
_CHAT_LIST_READY = '[aria-label="Chat list"]'
_CHAT_ROW        = "div[role='row']"
_SENDER          = "div._ak8l span.x1iyjqo2[title]"   # title attr = name
_PREVIEW         = "div._ak8k span[title]"              # title attr = message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_keyword(text: str) -> bool:
    words = set(re.findall(r"\w+", text.lower()))
    return bool(words & KEYWORDS)


def _item_id(sender: str, preview: str) -> str:
    raw = f"{sender}||{preview[:120]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _safe_slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text.strip())[:max_len]


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class WhatsAppWatcher(BaseWatcher):
    """Playwright-based watcher for WhatsApp Web keyword messages.

    Scans all visible chats for keyword matches. State file deduplication
    ensures each (sender, preview) pair only produces one action file.

    Each matched message produces:
        vault/Needs_Action/WHATSAPP_<slug>_<timestamp>.md
    """

    def __init__(self, vault_path: str, session_path: str, headless: bool = True):
        super().__init__(vault_path, check_interval=30)
        self._session_path = Path(session_path).expanduser().resolve()
        self._session_path.mkdir(parents=True, exist_ok=True)
        self._headless = headless
        self.logger.info(
            f"WhatsApp session dir : {self._session_path} | headless={headless}"
        )

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------

    def _scrape(self) -> list[dict]:
        results: list[dict] = []

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self._session_path),
                headless=self._headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                viewport={"width": 1400, "height": 900},
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()

                page.goto(
                    "https://web.whatsapp.com",
                    wait_until="networkidle",
                    timeout=60_000,
                )

                # Confirm the chat list is rendered (session valid)
                try:
                    page.wait_for_selector(_CHAT_LIST_READY, timeout=30_000)
                except PwTimeout:
                    self.logger.warning(
                        "WhatsApp chat list not found — session may need QR scan.\n"
                        "Re-run with WHATSAPP_HEADLESS=false to re-authenticate."
                    )
                    return []

                # Give WhatsApp's React app a moment to finish rendering rows
                page.wait_for_timeout(2_000)

                rows = page.query_selector_all(_CHAT_ROW)
                self.logger.info(f"WhatsApp: {len(rows)} chat rows visible.")

                for row in rows[:_MAX_CHATS]:
                    # Sender name
                    sender_el = row.query_selector(_SENDER)
                    if not sender_el:
                        continue
                    sender = (sender_el.get_attribute("title") or "").strip()
                    if not sender:
                        continue

                    # Message preview (title attr holds the full text)
                    preview_el = row.query_selector(_PREVIEW)
                    preview = ""
                    if preview_el:
                        # Prefer title attr (full text); fall back to inner_text
                        preview = (
                            preview_el.get_attribute("title")
                            or preview_el.inner_text()
                        ).strip()

                    if not (_has_keyword(preview) or _has_keyword(sender)):
                        continue

                    uid = _item_id(sender, preview)
                    if self._is_processed(uid):
                        continue

                    results.append(
                        {"id": uid, "sender": sender, "preview": preview}
                    )

            finally:
                context.close()

        return results

    # ------------------------------------------------------------------
    # BaseWatcher interface
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list:
        try:
            items = self._scrape()
            self.logger.info(f"WhatsApp: {len(items)} new keyword matches.")
            return items
        except Exception as exc:
            self.logger.error(f"WhatsApp scrape error: {exc}", exc_info=True)
            return []

    def create_action_file(self, item: dict) -> Path:
        timestamp = datetime.now(timezone.utc)
        ts_file   = timestamp.strftime("%Y%m%d_%H%M%S")
        ts_human  = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        slug      = _safe_slug(item["sender"])
        filepath  = self.needs_action / f"WHATSAPP_{slug}_{ts_file}.md"

        content = f"""\
---
type: message
source: whatsapp
sender: {item['sender']}
received: {timestamp.isoformat()}
priority: medium
status: pending
---

## WhatsApp Message from {item['sender']}

**Sender:** {item['sender']}
**Received:** {ts_human}

### Message Preview

{item['preview']}

## Suggested Actions

- [ ] Review message and determine appropriate response
- [ ] Reply via WhatsApp if required
- [ ] Escalate or forward if needed
- [ ] Move this file to /Done when complete
"""
        filepath.write_text(content, encoding="utf-8")
        self._mark_processed(item["id"])
        return filepath


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Watch WhatsApp Web for keyword messages "
            "and write structured Needs_Action files."
        )
    )
    parser.add_argument(
        "vault",
        help="Path to the Obsidian vault directory (e.g. ../vault)",
    )
    args = parser.parse_args()

    session = os.getenv("WHATSAPP_SESSION_PATH")
    if not session:
        print(
            "ERROR: WHATSAPP_SESSION_PATH is not set.\n"
            "Add it to your .env file: WHATSAPP_SESSION_PATH=/path/to/session_dir",
            file=sys.stderr,
        )
        sys.exit(1)

    headless = os.getenv("WHATSAPP_HEADLESS", "true").lower() != "false"
    watcher = WhatsAppWatcher(args.vault, session, headless=headless)
    watcher.run()


if __name__ == "__main__":
    main()
