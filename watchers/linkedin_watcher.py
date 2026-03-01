"""LinkedIn Watcher — uses Playwright to monitor LinkedIn for new direct
messages, connection requests, and lead keyword mentions, writing structured
Markdown action files to vault/Needs_Action/.

Architecture:
    - Opens a persistent Chromium context so the login session survives restarts.
    - Scrapes: /messaging/ for unread DMs, /mynetwork/invitation-manager/ for
      pending connection requests.
    - Applies lead keyword filter; marks high-priority files accordingly.
    - Deduplicates using SHA-256 of (kind, name, preview) stored in .state/.

Setup (one-time):
    1. Install Playwright browsers:
           playwright install chromium

    2. Set LINKEDIN_SESSION_PATH in your .env file:
           LINKEDIN_SESSION_PATH=/home/you/.linkedin_session

    3. First-run authentication (headless=False):
           LINKEDIN_HEADLESS=false python linkedin_watcher.py ../vault
       Log in to LinkedIn in the browser window that opens.
       After the feed loads, close the window — session is persisted.

    4. Subsequent runs work headless automatically.

Usage:
    python linkedin_watcher.py <vault_path>

PM2 (keep alive):
    pm2 start linkedin_watcher.py --interpreter python3 -- ../vault

Rate-limit note:
    LinkedIn rate-limits scrapers. The default check_interval is 300 s (5 min).
    Do not lower it significantly to avoid temporary account restrictions.

Selector stability note:
    LinkedIn's CSS class names change frequently. If selectors break, run with
    LINKEDIN_HEADLESS=false, inspect the page, and update the selector constants.
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

from base_watcher import BaseWatcher, with_retry

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Keywords that indicate a potential business lead
LEAD_KEYWORDS: frozenset[str] = frozenset(
    {
        "pricing",
        "price",
        "quote",
        "quotation",
        "service",
        "agency",
        "proposal",
        "rate",
        "rates",
        "hire",
        "hiring",
        "project",
        "budget",
        "contract",
    }
)

# Maximum items to inspect per page per cycle
_MAX_ITEMS = 25

# Check interval: 30 seconds
_CHECK_INTERVAL = 30

# ---------------------------------------------------------------------------
# Selectors — confirmed against LinkedIn 2026-02-21 via debug_linkedin.py
# ---------------------------------------------------------------------------

# Messaging page
_MSG_PAGE       = "https://www.linkedin.com/messaging/"
_MSG_CONVO_ROW  = "li.msg-conversation-listitem"          # ✓ confirmed (19 found)
_MSG_SENDER     = "h3.msg-conversation-listitem__participant-names span.truncate"  # ✓
_MSG_PREVIEW    = "p.msg-conversation-card__message-snippet"                       # ✓

# Network / invitations page — LinkedIn SPA needs extra render time
_CONN_PAGE      = "https://www.linkedin.com/mynetwork/invitation-manager/received/"
# Wait for any of these to confirm page render
_CONN_READY     = "div[class*='invitation'], li[class*='invitation'], div.mn-invitation-list"
_CONN_CARD      = "li[class*='invitation-'], div[class*='invitation-card']"
_CONN_NAME      = "span[class*='invitation'] span, div[class*='invitation'] span.t-bold, span.t-16.t-black"
_CONN_LINK      = "a[href*='/in/']"
_CONN_MSG       = "p[class*='message'], span[class*='message'], div[class*='custom-message']"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_lead_keyword(text: str) -> bool:
    words = set(re.findall(r"\w+", text.lower()))
    return bool(words & LEAD_KEYWORDS)


def _item_id(kind: str, name: str, snippet: str) -> str:
    raw = f"{kind}||{name}||{snippet[:120]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _safe_slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text.strip())[:max_len]


def _abs_url(href: str) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else f"https://www.linkedin.com{href}"


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class LinkedInWatcher(BaseWatcher):
    """Playwright-based watcher for LinkedIn DMs and connection requests.

    Produces files at:
        vault/Needs_Action/LINKEDIN_LEAD_<slug>_<ts>.md    (lead keyword hit)
        vault/Needs_Action/LINKEDIN_DM_<slug>_<ts>.md      (regular DM)
        vault/Needs_Action/LINKEDIN_CONN_<slug>_<ts>.md    (connection request)
    """

    def __init__(self, vault_path: str, session_path: str, headless: bool = True):
        super().__init__(vault_path, check_interval=_CHECK_INTERVAL)
        self._session_path = Path(session_path).expanduser().resolve()
        self._session_path.mkdir(parents=True, exist_ok=True)
        self._headless = headless
        self._consecutive_zero_msgs = 0   # health-check counter
        self.logger.info(
            f"LinkedIn session dir : {self._session_path} | headless={headless}"
        )

    # ------------------------------------------------------------------
    # Scraping helpers
    # ------------------------------------------------------------------

    def _is_logged_in(self, page) -> bool:
        """Return True if the current page is not a login/authwall redirect."""
        return not any(kw in page.url for kw in ("authwall", "/login", "/uas/login"))

    def _scrape_messages(self, page) -> list[dict]:
        results: list[dict] = []
        try:
            page.goto(_MSG_PAGE, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector(_MSG_CONVO_ROW, timeout=15_000)
        except PwTimeout:
            self.logger.warning("LinkedIn messaging page did not load in time.")
            return results

        rows = page.query_selector_all(_MSG_CONVO_ROW)
        if len(rows) == 0:
            self._consecutive_zero_msgs += 1
            if self._consecutive_zero_msgs >= 3:
                self.logger.warning(
                    f"No conversations found for {self._consecutive_zero_msgs} consecutive cycles — "
                    "session may have expired or selectors changed. "
                    "Re-run with LINKEDIN_HEADLESS=false to re-authenticate."
                )
        else:
            self._consecutive_zero_msgs = 0
        self.logger.info(f"LinkedIn messages: {len(rows)} conversation rows found.")

        for row in rows[:_MAX_ITEMS]:
            sender_el  = row.query_selector(_MSG_SENDER)
            preview_el = row.query_selector(_MSG_PREVIEW)

            sender  = sender_el.inner_text().strip()  if sender_el  else "Unknown"
            preview = preview_el.inner_text().strip() if preview_el else ""

            # Strip sender prefix LinkedIn sometimes adds (e.g. "Muhammad: Hi there")
            if ": " in preview and preview.split(": ")[0].strip() in sender:
                preview = preview.split(": ", 1)[1]

            if not _has_lead_keyword(preview):
                continue

            uid = _item_id("dm", sender, preview)
            if self._is_processed(uid):
                continue

            results.append(
                {
                    "id":      uid,
                    "kind":    "dm",
                    "name":    sender,
                    "profile": "",   # not available on messaging list page
                    "message": preview,
                    "is_lead": True,
                }
            )

        return results

    def _scrape_connections(self, page) -> list[dict]:
        results: list[dict] = []
        try:
            page.goto(_CONN_PAGE, wait_until="networkidle", timeout=30_000)
            # LinkedIn's SPA needs extra time to render invitation cards
            page.wait_for_timeout(5_000)
            page.wait_for_selector(_CONN_READY, timeout=10_000)
        except PwTimeout:
            self.logger.info("LinkedIn invitations page: no invitation cards found (may be empty).")
            return results

        cards = page.query_selector_all(_CONN_CARD)
        self.logger.info(f"LinkedIn connections: {len(cards)} invitation cards found.")

        for card in cards[:_MAX_ITEMS]:
            name_el = card.query_selector(_CONN_NAME)
            link_el = card.query_selector(_CONN_LINK)
            msg_el  = card.query_selector(_CONN_MSG)

            name    = name_el.inner_text().strip()  if name_el else "Unknown"
            profile = _abs_url(link_el.get_attribute("href") if link_el else "")
            message = msg_el.inner_text().strip()   if msg_el  else ""

            uid = _item_id("connection_request", name, profile)
            if self._is_processed(uid):
                continue

            results.append(
                {
                    "id":       uid,
                    "kind":     "connection_request",
                    "name":     name,
                    "profile":  profile,
                    "message":  message,
                    "is_lead":  _has_lead_keyword(message),
                }
            )

        return results

    def _wait_for_login(self, page) -> bool:
        """Keep the browser open and wait up to 3 minutes for manual login.

        Returns True if login succeeded, False if timed out.
        Called only when headless=False and the session is not authenticated.
        """
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Log in to LinkedIn in the browser window.")
        print("Waiting up to 3 minutes for you to complete login…")
        print("=" * 60 + "\n")

        # Poll every 2 seconds for up to 3 minutes
        for _ in range(90):
            try:
                page.wait_for_url(
                    lambda url: "feed" in url or "mynetwork" in url or "messaging" in url,
                    timeout=2_000,
                )
                self.logger.info("LinkedIn login detected — session saved.")
                return True
            except PwTimeout:
                pass  # keep waiting

        self.logger.error("Login wait timed out after 3 minutes.")
        return False

    def _launch_context(self, pw):
        """Launch a Playwright persistent context using the saved Chromium session.

        auth_linkedin.py saves the session using Playwright Chromium (not Chrome),
        so we must use the same Chromium here. Mixing Chrome and Chromium sessions
        causes immediate session invalidation by LinkedIn.
        """
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(self._session_path),
            headless=self._headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1440, "height": 900},
        )

    def _scrape_all(self) -> list[dict]:
        items: list[dict] = []

        with sync_playwright() as pw:
            context = self._launch_context(pw)
            try:
                page = context.pages[0] if context.pages else context.new_page()

                # Navigate to feed — LinkedIn will redirect to login if not authenticated
                page.goto(
                    "https://www.linkedin.com/feed/",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )

                if not self._is_logged_in(page):
                    if self._headless:
                        # Headless mode: can't do anything, bail out
                        self.logger.warning(
                            "LinkedIn session expired — re-authentication needed.\n"
                            "Re-run with LINKEDIN_HEADLESS=false to log in manually."
                        )
                        return []
                    else:
                        # Non-headless: keep browser open, wait for user to log in
                        if not self._wait_for_login(page):
                            return []
                        # After login, navigate to feed to confirm
                        page.goto(
                            "https://www.linkedin.com/feed/",
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )

                items.extend(self._scrape_messages(page))
                items.extend(self._scrape_connections(page))

            finally:
                context.close()

        return items

    # ------------------------------------------------------------------
    # BaseWatcher interface
    # ------------------------------------------------------------------

    @with_retry(max_attempts=3, base_delay=15.0, max_delay=120.0)
    def check_for_updates(self) -> list:
        items = self._scrape_all()
        self.logger.debug(f"LinkedIn: {len(items)} new items.")
        return items

    def create_action_file(self, item: dict) -> Path:
        timestamp = datetime.now(timezone.utc)
        ts_file   = timestamp.strftime("%Y%m%d_%H%M%S")
        ts_human  = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        slug      = _safe_slug(item["name"])

        is_lead   = item.get("is_lead", False)
        priority  = "high" if is_lead else "medium"

        kind_tag_map = {
            "dm":                 "DM",
            "connection_request": "CONN",
        }
        kind_tag = "LEAD" if is_lead else kind_tag_map.get(item["kind"], "LI")

        kind_label_map = {
            "dm":                 "Direct Message",
            "connection_request": "Connection Request",
        }
        kind_label = kind_label_map.get(item["kind"], item["kind"].replace("_", " ").title())

        filename = f"LINKEDIN_{kind_tag}_{slug}_{ts_file}.md"
        filepath = self.needs_action / filename

        message_text = item.get("message") or "_(no message provided)_"
        lead_action  = (
            "- [ ] Qualify as a lead and move to CRM\n"
            "- [ ] Draft a personalised reply\n"
        ) if is_lead else ""

        content = f"""\
---
type: lead
source: linkedin
kind: {item['kind']}
name: {item['name']}
profile: {item['profile']}
received: {timestamp.isoformat()}
priority: {priority}
is_lead: {str(is_lead).lower()}
status: pending
---

## LinkedIn {kind_label}: {item['name']}

**Profile:** {item['profile']}
**Received:** {ts_human}
**Priority:** {priority.upper()}{'  ⚡ LEAD DETECTED' if is_lead else ''}

### Message / Note

{message_text}

## Suggested Actions

- [ ] Review profile and message
- [ ] Accept or decline connection / reply to DM
{lead_action}- [ ] Move this file to /Done when complete
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
            "Watch LinkedIn for new DMs and connection requests "
            "and write structured Needs_Action files."
        )
    )
    parser.add_argument(
        "vault",
        help="Path to the Obsidian vault directory (e.g. ../vault)",
    )
    args = parser.parse_args()

    session = os.getenv("LINKEDIN_SESSION_PATH")
    if not session:
        print(
            "ERROR: LINKEDIN_SESSION_PATH is not set.\n"
            "Add it to your .env file: LINKEDIN_SESSION_PATH=/path/to/session_dir",
            file=sys.stderr,
        )
        sys.exit(1)

    headless = os.getenv("LINKEDIN_HEADLESS", "true").lower() != "false"
    watcher = LinkedInWatcher(args.vault, session, headless=headless)
    watcher.run()


if __name__ == "__main__":
    main()
