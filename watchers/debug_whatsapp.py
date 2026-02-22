"""
WhatsApp Web selector diagnostics.

Opens your saved WhatsApp session, waits for the chat list to fully render,
then prints every candidate selector result and saves a screenshot + the
inner HTML of the chat list so you can inspect the real DOM structure.

Usage:
    python debug_whatsapp.py <vault_path>

Output (written next to this script):
    debug_whatsapp_screenshot.png   — full-page screenshot
    debug_whatsapp_chatlist.html    — innerHTML of the chat list panel
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

load_dotenv()

SCRIPT_DIR = Path(__file__).parent

# ── Selectors to probe ────────────────────────────────────────────────────────
PROBE_SELECTORS = {
    # ── Chat list container ───────────────────────────────────────────────
    "chat_list_aria":       '[aria-label="Chat list"]',          # confirmed 2026-02-21

    # ── Chat row (CONFIRMED WORKING) ──────────────────────────────────────
    "row_role":             "div[role='row']",                   # ✓ confirmed
    "listitem_role":        "div[role='listitem']",              # ✗ broken

    # ── Sender name (CONFIRMED WORKING) ───────────────────────────────────
    "sender_confirmed":     "div._ak8l span.x1iyjqo2[title]",   # ✓ confirmed
    "span_title_any":       "span[title]",                       # broad fallback

    # ── Message preview (CONFIRMED WORKING) ───────────────────────────────
    "preview_confirmed":    "div._ak8k span[title]",             # ✓ title attr = full text
    "preview_inner":        "div._ak8k",                         # inner_text fallback

    # ── Unread indicators (none reliably in DOM) ──────────────────────────
    "unread_aria":          "[aria-label*='unread']",            # page-level total only
    "unread_data_icon":     "span[data-icon='unread-count']",
}
# ─────────────────────────────────────────────────────────────────────────────


def main():
    session = os.getenv("WHATSAPP_SESSION_PATH")
    if not session:
        print("ERROR: WHATSAPP_SESSION_PATH not set in .env", file=sys.stderr)
        sys.exit(1)

    session_path = Path(session).expanduser().resolve()
    if not session_path.exists():
        print(f"ERROR: session directory not found: {session_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Session dir : {session_path}")
    print("Opening WhatsApp Web — please wait…\n")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=False,
            args=["--no-sandbox"],
            viewport={"width": 1400, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="networkidle", timeout=60_000)

        # Wait generously for WhatsApp to fully render
        print("Waiting 15 s for WhatsApp to fully render…")
        page.wait_for_timeout(15_000)

        # ── Screenshot ───────────────────────────────────────────────────────
        screenshot_path = SCRIPT_DIR / "debug_whatsapp_screenshot.png"
        page.screenshot(path=str(screenshot_path), full_page=False)
        print(f"Screenshot saved → {screenshot_path}\n")

        # ── Probe every selector ─────────────────────────────────────────────
        print("=" * 60)
        print("SELECTOR PROBE RESULTS")
        print("=" * 60)
        for name, selector in PROBE_SELECTORS.items():
            try:
                elements = page.query_selector_all(selector)
                count = len(elements)
                sample = ""
                if count > 0:
                    try:
                        txt = elements[0].inner_text()[:80].replace("\n", " ")
                        sample = f'  → first: "{txt}"'
                    except Exception:
                        pass
                status = f"FOUND {count:3d}" if count else "      0    "
                print(f"  {status}  {name:30s}  {selector}{sample}")
            except Exception as exc:
                print(f"  ERROR         {name:30s}  {exc}")

        print()

        # ── Dump chat list HTML ──────────────────────────────────────────────
        chat_list_html = ""
        for sel in [
            '[aria-label="Chat list"]',
            '[data-testid="chat-list"]',
            "div#pane-side",
        ]:
            el = page.query_selector(sel)
            if el:
                chat_list_html = el.inner_html()
                print(f"Chat list HTML captured with selector: {sel!r}")
                print(f"  Length: {len(chat_list_html):,} chars")
                break

        if chat_list_html:
            html_path = SCRIPT_DIR / "debug_whatsapp_chatlist.html"
            html_path.write_text(chat_list_html, encoding="utf-8")
            print(f"  Saved → {html_path}")
        else:
            print("WARNING: No chat list element found with any known selector.")
            # Fall back to full page HTML
            full_html_path = SCRIPT_DIR / "debug_whatsapp_fullpage.html"
            full_html_path.write_text(page.content(), encoding="utf-8")
            print(f"  Full page HTML saved → {full_html_path}")

        print()

        # ── Print page title & URL for confirmation ───────────────────────────
        print(f"Page title : {page.title()}")
        print(f"Page URL   : {page.url}")
        print()
        print("Done. Check debug_whatsapp_screenshot.png to confirm WhatsApp loaded.")
        print("If logged out (shows QR code), re-run whatsapp_watcher.py with WHATSAPP_HEADLESS=false to re-authenticate.")

        context.close()


if __name__ == "__main__":
    main()
