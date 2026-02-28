#!/usr/bin/env python3
"""
LinkedIn DM Sender — AI Employee Silver Tier skill script.

Sends a direct message to a LinkedIn connection by navigating to their
profile and using the Message button. Handles both:
  - Direct thread (Message opens an existing conversation)
  - Compose dialog (Message opens "New message" with a recipient search box)

Usage:
    python send_dm.py --to-profile https://linkedin.com/in/username --message "Hi there"
    python send_dm.py --to-profile https://linkedin.com/in/username --message "Hi" --dry-run
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

_DEFAULT_SESSION = Path(os.environ.get("LINKEDIN_SESSION_PATH", "~/.sessions/linkedin")).expanduser()


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_dm(
    to_profile: str,
    message: str,
    session_path: Path,
    headless: bool = True,
    dry_run: bool = False,
) -> dict:
    if not message.strip():
        return {"status": "error", "error": "Message is empty.", "timestamp": _ts()}

    if dry_run:
        return {
            "status":    "dry_run",
            "to":        to_profile,
            "preview":   message[:120] + ("..." if len(message) > 120 else ""),
            "timestamp": _ts(),
        }

    if not session_path.exists():
        return {
            "status": "error",
            "error":  f"LinkedIn session not found: {session_path}. Run: python watchers/auth_linkedin.py",
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
            # ── Navigate to profile ───────────────────────────────────────
            page.goto(to_profile, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)

            if "authwall" in page.url or "login" in page.url:
                return {
                    "status": "error",
                    "error":  "LinkedIn session expired. Run: python watchers/auth_linkedin.py",
                    "timestamp": _ts(),
                }

            # ── Grab recipient name from profile page ─────────────────────
            recipient_name = ""
            try:
                recipient_name = page.locator("h1").first.inner_text(timeout=5000).strip()
            except Exception:
                pass

            # ── Click Message button ──────────────────────────────────────
            msg_selectors = [
                "button[aria-label*='Message']",
                "a[aria-label*='Message']",
                "button:has-text('Message')",
                ".pvs-profile-actions button:has-text('Message')",
            ]
            clicked = False
            for sel in msg_selectors:
                try:
                    page.click(sel, timeout=5000)
                    clicked = True
                    break
                except PlaywrightTimeout:
                    continue

            if not clicked:
                page.screenshot(path="/tmp/linkedin_dm_debug.png")
                return {
                    "status": "error",
                    "error":  "Could not find Message button on profile. Screenshot: /tmp/linkedin_dm_debug.png",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1500)

            # ── Handle compose dialog (New message with recipient search) ──
            # LinkedIn opens this when messaging yourself or in some contexts
            compose_input_selectors = [
                "input[placeholder*='name']",
                "input[placeholder*='Name']",
                ".msg-connections-typeahead__search-input",
                "input.msg-compose-recipient-input",
            ]
            compose_found = False
            for sel in compose_input_selectors:
                if page.locator(sel).count() > 0:
                    compose_found = True
                    # Type recipient name to search
                    search_name = recipient_name.split()[0] if recipient_name else ""
                    if not search_name:
                        page.screenshot(path="/tmp/linkedin_dm_debug.png")
                        return {
                            "status": "error",
                            "error":  "Compose dialog appeared but could not determine recipient name.",
                            "timestamp": _ts(),
                        }
                    page.click(sel, timeout=3000)
                    page.keyboard.type(search_name, delay=100)
                    page.wait_for_timeout(1500)

                    # Click the first matching suggestion
                    suggestion_selectors = [
                        "li.msg-connections-typeahead__connection",
                        "li.search-typeahead-v2__hit",
                        "div[data-view-name='search-typeahead-hit']",
                        "ul li:first-child",
                    ]
                    selected = False
                    for sug_sel in suggestion_selectors:
                        try:
                            page.click(sug_sel, timeout=3000)
                            selected = True
                            break
                        except PlaywrightTimeout:
                            continue

                    if not selected:
                        page.screenshot(path="/tmp/linkedin_dm_debug.png")
                        return {
                            "status": "error",
                            "error":  f"Could not select '{search_name}' from compose suggestions. Screenshot: /tmp/linkedin_dm_debug.png",
                            "timestamp": _ts(),
                        }
                    page.wait_for_timeout(1000)
                    break

            # ── Find message input and type ───────────────────────────────
            msg_input_selectors = [
                "div.msg-form__contenteditable[contenteditable='true']",
                "div[role='textbox'][contenteditable='true']",
                "div.msg-form__contenteditable",
                "div[data-placeholder]",
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
                page.screenshot(path="/tmp/linkedin_dm_debug.png")
                return {
                    "status": "error",
                    "error":  "Could not find message input box. Screenshot: /tmp/linkedin_dm_debug.png",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(500)

            # ── Send ──────────────────────────────────────────────────────
            send_selectors = [
                "button.msg-form__send-button",
                "button[aria-label='Send']",
                "button[type='submit']:has-text('Send')",
            ]
            sent = False
            for sel in send_selectors:
                try:
                    page.click(sel, timeout=5000)
                    sent = True
                    break
                except PlaywrightTimeout:
                    continue

            if not sent:
                try:
                    page.keyboard.press("Enter")
                    sent = True
                except Exception:
                    pass

            if not sent:
                return {"status": "error", "error": "Could not find Send button.", "timestamp": _ts()}

            page.wait_for_timeout(2000)

            screenshot_path = f"/tmp/linkedin_dm_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=screenshot_path)

            return {
                "status":           "sent",
                "to":               to_profile,
                "to_name":          recipient_name,
                "screenshot":       screenshot_path,
                "message_preview":  message[:80] + ("..." if len(message) > 80 else ""),
                "timestamp":        _ts(),
            }

        except PlaywrightTimeout as exc:
            return {"status": "error", "error": f"Timeout: {exc}", "timestamp": _ts()}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "timestamp": _ts()}
        finally:
            context.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LinkedIn DM Sender — AI Employee Silver Tier action script."
    )
    parser.add_argument("--to-profile", required=True,
                        help="LinkedIn profile URL (e.g. https://linkedin.com/in/username)")
    parser.add_argument("--message",    required=True,
                        help="Message text to send")
    parser.add_argument("--session-path",
                        default=str(_DEFAULT_SESSION),
                        help=f"Path to LinkedIn Playwright session (default: {_DEFAULT_SESSION})")
    parser.add_argument("--headless",    action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()

    session_path = Path(args.session_path).expanduser().resolve()

    result = send_dm(
        to_profile   = args.to_profile,
        message      = args.message,
        session_path = session_path,
        headless     = args.headless,
        dry_run      = args.dry_run,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("sent", "dry_run") else 1)


if __name__ == "__main__":
    main()
