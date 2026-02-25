#!/usr/bin/env python3
"""
WhatsApp Sender — AI Employee Silver Tier skill script.

Sends a WhatsApp message to a named contact using an existing WhatsApp Web
Playwright session. Must only be called after human approval via vault/Approved/.

Usage:
    python send_message.py --to "Contact Name" --message "Hello!" --session-path ~/.sessions/whatsapp
    python send_message.py --to "Contact Name" --message-file /tmp/msg.txt
    python send_message.py --to "Contact Name" --message "Hello!" --dry-run
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    sys.exit("Missing dependency. Run:  pip install playwright && playwright install chromium")

_DEFAULT_SESSION = Path(os.environ.get("WHATSAPP_SESSION_PATH", "~/.sessions/whatsapp")).expanduser()
_WA_URL          = "https://web.whatsapp.com"
_MAX_MSG_LEN     = 65536  # WhatsApp message limit


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_message(
    to: str,
    message: str,
    session_path: Path,
    headless: bool = True,
    dry_run: bool = False,
) -> dict:
    if not message.strip():
        return {"status": "error", "error": "Message is empty.", "timestamp": _ts()}

    if dry_run:
        return {
            "status":   "dry_run",
            "to":       to,
            "preview":  message[:120] + ("..." if len(message) > 120 else ""),
            "timestamp": _ts(),
        }

    if not session_path.exists():
        return {
            "status":  "error",
            "error":   f"WhatsApp session not found: {session_path}\nRun: WHATSAPP_HEADLESS=false python watchers/whatsapp_watcher.py ./vault",
            "timestamp": _ts(),
        }

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(session_path),
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            # ── Navigate to WhatsApp Web ──────────────────────────────────
            page.goto(_WA_URL, wait_until="domcontentloaded", timeout=30_000)

            # ── Wait for chat list (confirms login) ───────────────────────
            try:
                page.wait_for_selector("div[role='row']", timeout=20_000)
            except PlaywrightTimeout:
                if "qrcode" in page.url or page.locator("canvas[aria-label='Scan me!']").count() > 0:
                    return {
                        "status":  "error",
                        "error":   "WhatsApp session expired — QR scan required. Run watcher in headed mode.",
                        "timestamp": _ts(),
                    }
                return {"status": "error", "error": "Chat list did not load.", "timestamp": _ts()}

            # ── Search for the contact ────────────────────────────────────
            search_selectors = [
                "div[contenteditable='true'][data-tab='3']",
                "div[role='textbox'][title='Search input textbox']",
                "span[data-icon='search']",
            ]
            searched = False
            for sel in search_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.keyboard.type(to, delay=50)
                    searched = True
                    break
                except PlaywrightTimeout:
                    continue

            if not searched:
                return {"status": "error", "error": "Could not find WhatsApp search box.", "timestamp": _ts()}

            page.wait_for_timeout(1500)

            # ── Click matching conversation ───────────────────────────────
            # Look for a row whose sender title contains the contact name
            rows = page.locator("div[role='row']").all()
            matched = False
            for row in rows:
                try:
                    title = row.locator("span.x1iyjqo2[title]").first.get_attribute("title", timeout=500) or ""
                    if to.lower() in title.lower():
                        row.click()
                        matched = True
                        break
                except Exception:
                    continue

            if not matched:
                return {
                    "status":  "error",
                    "error":   f"Contact '{to}' not found in WhatsApp chat list. Check the exact display name.",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1000)

            # ── Type the message ──────────────────────────────────────────
            msg_input_selectors = [
                "div[contenteditable='true'][data-tab='10']",
                "div[role='textbox'][title='Type a message']",
                "footer div[contenteditable='true']",
            ]
            typed = False
            for sel in msg_input_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.keyboard.type(message, delay=20)
                    typed = True
                    break
                except PlaywrightTimeout:
                    continue

            if not typed:
                return {"status": "error", "error": "Could not locate message input field.", "timestamp": _ts()}

            # ── Send ──────────────────────────────────────────────────────
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)

            # ── Confirm sent (look for outgoing message bubble) ───────────
            try:
                page.wait_for_selector(
                    "span[data-icon='msg-check'], span[data-icon='msg-dblcheck']",
                    timeout=8000
                )
                delivered = True
            except PlaywrightTimeout:
                delivered = False  # message may still have sent — not an error

            return {
                "status":    "sent",
                "to":        to,
                "delivered": delivered,
                "preview":   message[:80] + ("..." if len(message) > 80 else ""),
                "timestamp": _ts(),
            }

        except PlaywrightTimeout as exc:
            return {"status": "error", "error": f"Timeout: {exc}", "timestamp": _ts()}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "timestamp": _ts()}
        finally:
            context.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WhatsApp Sender — AI Employee Silver Tier action script."
    )
    parser.add_argument("--to",            required=True,
                        help="Contact display name exactly as shown in WhatsApp")
    parser.add_argument("--message",       help="Message text to send")
    parser.add_argument("--message-file",  help="Path to file containing message text")
    parser.add_argument(
        "--session-path",
        default=str(_DEFAULT_SESSION),
        help=f"Path to WhatsApp Playwright session (default: {_DEFAULT_SESSION})",
    )
    parser.add_argument("--headless",    action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="Show browser window")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Log intended action without sending")
    args = parser.parse_args()

    message = args.message or ""
    if args.message_file:
        mf = Path(args.message_file).expanduser()
        if not mf.exists():
            print(json.dumps({"status": "error", "error": f"message-file not found: {mf}"}))
            sys.exit(1)
        message = mf.read_text(encoding="utf-8")

    if not message.strip():
        parser.error("Message is empty. Provide --message or --message-file.")

    session_path = Path(args.session_path).expanduser().resolve()

    result = send_message(
        to           = args.to,
        message      = message,
        session_path = session_path,
        headless     = args.headless,
        dry_run      = args.dry_run,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("sent", "dry_run") else 1)


if __name__ == "__main__":
    main()
