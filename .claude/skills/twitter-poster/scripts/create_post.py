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
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    sys.exit("Missing dependency. Run:  pip install playwright && playwright install chromium")

try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

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
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()
        if stealth_sync:
            stealth_sync(page)

        try:
            # ── Navigate to home and check login state ────────────────────
            page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3000)

            if any(kw in page.url for kw in ("login", "signin", "i/flow")):
                return {
                    "status":    "error",
                    "error":     "Twitter/X session expired. Run: python watchers/auth_twitter.py",
                    "timestamp": _ts(),
                }

            # ── Human-like browsing simulation before posting ─────────────
            # Scroll down and back up, move mouse, pause — mimics a real user
            # reading their feed before composing a post.
            for _ in range(random.randint(3, 5)):
                scroll_by = random.randint(200, 500)
                page.evaluate(f"window.scrollBy(0, {scroll_by})")
                page.wait_for_timeout(random.randint(800, 1800))

            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(random.randint(1000, 2000))

            # Random mouse movement across the feed
            for _ in range(random.randint(2, 4)):
                x = random.randint(300, 800)
                y = random.randint(200, 600)
                page.mouse.move(x, y)
                page.wait_for_timeout(random.randint(300, 700))

            # ── Use inline home-feed composer (less detectable than /compose/tweet) ──
            # Click the "What's happening?" placeholder on the home feed
            inline_selectors = [
                "div[data-testid='tweetTextarea_0']",
                "div[aria-label='Post text']",
                "div[data-testid='tweetTextarea_0_label']",
                "div[role='textbox'][aria-label*='Post']",
                "div[role='textbox'][aria-label*='Tweet']",
                "div[role='textbox'][aria-label*='tweet']",
            ]
            typed = False
            for sel in inline_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.wait_for_timeout(1000)
                    page.keyboard.type(content, delay=50)
                    typed = True
                    break
                except PlaywrightTimeout:
                    continue

            if not typed:
                # Fallback: click "Post" button in left sidebar to open modal
                try:
                    page.click("a[data-testid='SideNav_NewTweet_Button']", timeout=5000)
                    page.wait_for_timeout(1500)
                    page.click("div[data-testid='tweetTextarea_0']", timeout=5000)
                    page.wait_for_timeout(500)
                    page.keyboard.type(content, delay=50)
                    typed = True
                except PlaywrightTimeout:
                    pass

            if not typed:
                screenshot_path = f"/tmp/twitter_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not find tweet editor. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1500)

            # ── Wait for Post button to become enabled, then use keyboard ──
            # X detects programmatic button clicks as automation — Ctrl+Enter
            # is the safest submission method.
            posted = False
            try:
                page.wait_for_function(
                    "document.querySelector(\"button[data-testid='tweetButton']\")?.disabled === false"
                    " || document.querySelector(\"button[data-testid='tweetButtonInline']\")?.disabled === false",
                    timeout=6000,
                )
            except Exception:
                pass

            try:
                page.keyboard.press("Control+Return")
                page.wait_for_timeout(2000)
                posted = True
            except Exception:
                pass

            if not posted:
                # Fallback: JS click to bypass automation detection
                try:
                    page.evaluate(
                        "document.querySelector(\"button[data-testid='tweetButton']\")?.click()"
                        " || document.querySelector(\"button[data-testid='tweetButtonInline']\")?.click()"
                    )
                    page.wait_for_timeout(2000)
                    posted = True
                except Exception:
                    pass

            if not posted:
                screenshot_path = f"/tmp/twitter_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not click Post button. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            # ── Wait for post to complete ──────────────────────────────────
            page.wait_for_timeout(3000)
            success = "compose" not in page.url

            screenshot_path = f"/tmp/twitter_post_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=screenshot_path)

            if not success:
                return {
                    "status":     "error",
                    "error":      "Post may not have submitted — still on compose page.",
                    "screenshot": screenshot_path,
                    "timestamp":  _ts(),
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
        description="Twitter/X Poster — Playwright session-based action script."
    )
    parser.add_argument("--content",      help="Tweet text (max 280 chars)")
    parser.add_argument("--content-file", help="Path to file containing tweet text")
    parser.add_argument(
        "--session-path",
        default=str(_DEFAULT_SESSION),
        help=f"Path to Twitter/X Playwright session (default: {_DEFAULT_SESSION})",
    )
    parser.add_argument("--headless",    action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="Show browser window")
    parser.add_argument("--dry-run",     action="store_true", help="Preview without posting")
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
