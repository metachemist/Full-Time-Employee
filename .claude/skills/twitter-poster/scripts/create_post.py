#!/usr/bin/env python3
"""
Twitter/X Poster — AI Employee Gold Tier skill script.

Publishes a post (tweet) to X.com using a persistent Playwright session.
Must only be called after human approval via vault/Approved/.

Usage:
    python create_post.py --content "Tweet text..." --session-path ~/.sessions/twitter
    python create_post.py --content-file /tmp/tweet.txt --session-path ~/.sessions/twitter
    python create_post.py --content "Tweet text..." --dry-run
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

_DEFAULT_SESSION = Path(os.environ.get("TWITTER_SESSION_PATH", "~/.sessions/twitter")).expanduser()
_COMPOSE_URL     = "https://x.com/compose/tweet"
_HOME_URL        = "https://x.com/home"
_MAX_CONTENT_LEN = 280  # Standard X character limit


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_post(
    content: str,
    session_path: Path,
    headless: bool = True,
    dry_run: bool = False,
) -> dict:
    if not content.strip():
        return {"status": "error", "error": "Post content is empty.", "timestamp": _ts()}

    if len(content) > _MAX_CONTENT_LEN:
        return {
            "status":    "error",
            "error":     f"Content exceeds X character limit ({len(content)}/{_MAX_CONTENT_LEN} chars).",
            "timestamp": _ts(),
        }

    if dry_run:
        return {
            "status":       "dry_run",
            "content_len":  len(content),
            "preview":      content[:120] + ("..." if len(content) > 120 else ""),
            "timestamp":    _ts(),
        }

    if not session_path.exists():
        return {
            "status":    "error",
            "error":     f"Twitter session not found: {session_path}. Run: python watchers/auth_twitter.py",
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
            # ── Navigate to home first to check login state ───────────────
            page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)

            if any(kw in page.url for kw in ("login", "signin", "i/flow")):
                return {
                    "status":    "error",
                    "error":     "Twitter/X session expired. Run: python watchers/auth_twitter.py",
                    "timestamp": _ts(),
                }

            # ── Navigate directly to compose URL ──────────────────────────
            # x.com/compose/tweet is the most reliable compose entry point
            page.goto(_COMPOSE_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)

            # ── Find and click the tweet text area ────────────────────────
            editor_selectors = [
                "div[data-testid='tweetTextarea_0']",
                "div[data-testid='tweetTextarea_0_label']",
                "div[role='textbox'][aria-label*='tweet']",
                "div[role='textbox'][aria-label*='Tweet']",
                "div[role='textbox'][aria-label*='Post']",
                "div.public-DraftEditor-content",
            ]
            typed = False
            for sel in editor_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.keyboard.type(content, delay=30)
                    typed = True
                    break
                except PlaywrightTimeout:
                    continue

            if not typed:
                screenshot_path = f"/tmp/twitter_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not find tweet editor. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1000)

            # ── Click Post / Tweet button ──────────────────────────────────
            post_button_selectors = [
                "button[data-testid='tweetButtonInline']",
                "button[data-testid='tweetButton']",
                "div[data-testid='tweetButtonInline']",
            ]
            posted = False
            for sel in post_button_selectors:
                try:
                    btn = page.locator(sel).first
                    btn.wait_for(state="visible", timeout=5000)
                    btn.click(timeout=5000)
                    posted = True
                    break
                except (PlaywrightTimeout, Exception):
                    continue

            if not posted:
                # Fallback: keyboard shortcut Ctrl+Enter
                try:
                    page.keyboard.press("Control+Return")
                    posted = True
                except Exception:
                    pass

            if not posted:
                return {
                    "status":    "error",
                    "error":     "Could not find Post/Tweet button.",
                    "timestamp": _ts(),
                }

            # ── Wait for post to complete ──────────────────────────────────
            page.wait_for_timeout(3000)

            # Confirm: should navigate away from compose URL on success
            success = "compose" not in page.url

            # ── Screenshot for audit ──────────────────────────────────────
            screenshot_path = f"/tmp/twitter_post_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=screenshot_path)

            if not success:
                return {
                    "status":    "error",
                    "error":     "Post may not have submitted — still on compose page.",
                    "screenshot": screenshot_path,
                    "timestamp": _ts(),
                }

            return {
                "status":          "posted",
                "profile_url":     "https://x.com/home",
                "screenshot":      screenshot_path,
                "content_preview": content[:80] + ("..." if len(content) > 80 else ""),
                "timestamp":       _ts(),
            }

        except PlaywrightTimeout as exc:
            return {"status": "error", "error": f"Timeout: {exc}", "timestamp": _ts()}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "timestamp": _ts()}
        finally:
            context.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Twitter/X Poster — AI Employee Gold Tier action script."
    )
    parser.add_argument("--content",      help="Tweet text (max 280 chars)")
    parser.add_argument("--content-file", help="Path to file containing tweet text")
    parser.add_argument(
        "--session-path",
        default=str(_DEFAULT_SESSION),
        help=f"Path to Twitter/X Playwright session (default: {_DEFAULT_SESSION})",
    )
    parser.add_argument("--headless",    action="store_true", default=True,
                        help="Run headless (default: True)")
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="Show browser window")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Preview without posting")
    args = parser.parse_args()

    content = args.content or ""
    if args.content_file:
        cf = Path(args.content_file).expanduser()
        if not cf.exists():
            print(json.dumps({"status": "error", "error": f"content-file not found: {cf}"}))
            sys.exit(1)
        content = cf.read_text(encoding="utf-8")

    if not content.strip():
        parser.error("Tweet content is empty. Provide --content or --content-file.")

    session_path = Path(args.session_path).expanduser().resolve()

    result = create_post(
        content      = content,
        session_path = session_path,
        headless     = args.headless,
        dry_run      = args.dry_run,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("posted", "dry_run") else 1)


if __name__ == "__main__":
    main()
