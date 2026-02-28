#!/usr/bin/env python3
"""
LinkedIn Poster — AI Employee Silver Tier skill script.

Publishes a post to LinkedIn using the existing Playwright session.
Must only be called after human approval via vault/Approved/.

Usage:
    python create_post.py --content "Post text..." --session-path ~/.sessions/linkedin
    python create_post.py --content-file /tmp/post.txt --session-path ~/.sessions/linkedin
    python create_post.py --content "Post text..." --dry-run
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    sys.exit("Missing dependency. Run:  pip install playwright && playwright install chromium")

_DEFAULT_SESSION = Path(os.environ.get("LINKEDIN_SESSION_PATH", "~/.sessions/linkedin")).expanduser()
_POST_URL        = "https://www.linkedin.com/feed/"
_MAX_CONTENT_LEN = 3000  # LinkedIn post character limit


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
            "error":     f"Content exceeds LinkedIn limit ({len(content)}/{_MAX_CONTENT_LEN} chars).",
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
            "error":     f"LinkedIn session not found: {session_path}. Run: python watchers/auth_linkedin.py",
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
            # ── Navigate to feed ─────────────────────────────────────────
            page.goto(_POST_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)

            # ── Check login state ─────────────────────────────────────────
            if "authwall" in page.url or "login" in page.url:
                return {
                    "status":    "error",
                    "error":     "LinkedIn session expired. Run: python watchers/auth_linkedin.py",
                    "timestamp": _ts(),
                }

            # ── Open post modal ───────────────────────────────────────────
            start_post_selectors = [
                "button[aria-label='Start a post']",
                "div.share-box-feed-entry__trigger",
                "[data-control-name='share.sharebox_post_button']",
                "span:has-text('Start a post')",
            ]
            clicked = False
            for sel in start_post_selectors:
                try:
                    page.click(sel, timeout=5000)
                    clicked = True
                    break
                except PlaywrightTimeout:
                    continue

            if not clicked:
                # Take screenshot for debugging
                page.screenshot(path="/tmp/linkedin_post_debug.png")
                return {
                    "status":    "error",
                    "error":     "Could not find 'Start a post' button. Screenshot saved to /tmp/linkedin_post_debug.png",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1500)

            # ── Type post content ─────────────────────────────────────────
            editor_selectors = [
                "div.ql-editor[contenteditable='true']",
                "div[role='textbox'][contenteditable='true']",
                "div.mentions-texteditor__contenteditable",
            ]
            typed = False
            for sel in editor_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.keyboard.type(content, delay=20)
                    typed = True
                    break
                except PlaywrightTimeout:
                    continue

            if not typed:
                return {
                    "status":    "error",
                    "error":     "Could not locate post text editor. LinkedIn may have changed its UI.",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1000)

            # ── Click Post button ─────────────────────────────────────────
            post_button_selectors = [
                "button.share-actions__primary-action",
                "button[aria-label='Post']",
                "button:has-text('Post')",
            ]
            posted = False
            for sel in post_button_selectors:
                try:
                    page.click(sel, timeout=5000)
                    posted = True
                    break
                except PlaywrightTimeout:
                    continue

            if not posted:
                return {
                    "status":    "error",
                    "error":     "Could not find Post button.",
                    "timestamp": _ts(),
                }

            # ── Wait for post to appear in feed ───────────────────────────
            # LinkedIn feed continuously polls so networkidle rarely fires;
            # catch the timeout and fall through — post was already submitted.
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            page.wait_for_timeout(2000)

            # ── Grab post URL (best effort) ───────────────────────────────
            post_url = None
            try:
                # Most recent post appears at top of feed
                post_url = page.locator(
                    "article.feed-shared-update-v2"
                ).first.get_attribute("data-urn")
                if post_url:
                    post_url = f"https://www.linkedin.com/feed/update/{post_url}/"
            except Exception:
                post_url = _POST_URL  # fallback

            # ── Screenshot for audit ──────────────────────────────────────
            screenshot_path = f"/tmp/linkedin_post_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=screenshot_path)

            return {
                "status":          "posted",
                "post_url":        post_url or _POST_URL,
                "screenshot":      screenshot_path,
                "content_preview": content[:80] + "...",
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
        description="LinkedIn Poster — AI Employee Silver Tier action script."
    )
    parser.add_argument("--content",      help="Post text (use quotes for multi-line)")
    parser.add_argument("--content-file", help="Path to file containing post text")
    parser.add_argument(
        "--session-path",
        default=str(_DEFAULT_SESSION),
        help=f"Path to LinkedIn Playwright session (default: {_DEFAULT_SESSION})",
    )
    parser.add_argument("--headless",  action="store_true", default=True,
                        help="Run headless (default: True)")
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="Show browser window")
    parser.add_argument("--dry-run",   action="store_true",
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
        parser.error("Post content is empty. Provide --content or --content-file.")

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
