#!/usr/bin/env python3
"""
Facebook Poster — AI Employee Gold Tier skill script.

Publishes a text post to Facebook using a persistent Playwright session.
Must only be called after human approval via vault/Approved/.

Usage:
    python create_post.py --content "Post text..." --session-path ~/.sessions/facebook
    python create_post.py --content-file /tmp/post.txt --session-path ~/.sessions/facebook
    python create_post.py --content "Post text..." --dry-run
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

_DEFAULT_SESSION = Path(os.environ.get("FACEBOOK_SESSION_PATH", "~/.sessions/facebook")).expanduser()
_FEED_URL        = "https://www.facebook.com/"
_MAX_CONTENT_LEN = 63206  # Facebook's post character limit


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
            "error":     f"Content exceeds Facebook limit ({len(content)}/{_MAX_CONTENT_LEN} chars).",
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
            "error":     f"Facebook session not found: {session_path}. Run: python watchers/auth_facebook.py",
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

        try:
            # ── Navigate to feed ──────────────────────────────────────────
            page.goto(_FEED_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3000)

            # ── Check login state ─────────────────────────────────────────
            if "login" in page.url or "checkpoint" in page.url:
                return {
                    "status":    "error",
                    "error":     "Facebook session expired. Run: python watchers/auth_facebook.py",
                    "timestamp": _ts(),
                }

            # ── Click the post composer trigger ───────────────────────────
            composer_selectors = [
                "[aria-placeholder*='on your mind']",
                "[aria-label='Create a post']",
                "div[role='button'][tabindex='0']:has-text(\"What's on your mind\")",
                "span:has-text(\"What's on your mind\")",
            ]
            clicked = False
            for sel in composer_selectors:
                try:
                    page.click(sel, timeout=5000)
                    clicked = True
                    break
                except PlaywrightTimeout:
                    continue

            if not clicked:
                screenshot_path = f"/tmp/facebook_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not find post composer. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1500)

            # ── Type post content in the modal ────────────────────────────
            modal_selectors = [
                "div[role='dialog'] div[contenteditable='true']",
                "div[contenteditable='true'][aria-placeholder*='on your mind']",
                "div[contenteditable='true']",
            ]
            typed = False
            for sel in modal_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.keyboard.type(content, delay=20)
                    typed = True
                    break
                except PlaywrightTimeout:
                    continue

            if not typed:
                screenshot_path = f"/tmp/facebook_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not find post editor. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1000)

            # ── Click the Post button ─────────────────────────────────────
            posted = False
            try:
                page.wait_for_function(
                    """() => {
                        const btn = document.querySelector("div[aria-label='Post']") ||
                                    document.querySelector("[aria-label='Post'][role='button']");
                        return btn && !btn.getAttribute('aria-disabled');
                    }""",
                    timeout=5000,
                )
                post_btn_selectors = [
                    "div[aria-label='Post'][role='button']",
                    "[aria-label='Post'][role='button']",
                    "div[role='dialog'] div[aria-label='Post']",
                ]
                for sel in post_btn_selectors:
                    try:
                        page.click(sel, timeout=5000)
                        posted = True
                        break
                    except PlaywrightTimeout:
                        continue
            except Exception:
                pass

            if not posted:
                screenshot_path = f"/tmp/facebook_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not click Post button. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            # ── Wait for post to be published ─────────────────────────────
            page.wait_for_timeout(4000)

            screenshot_path = f"/tmp/facebook_post_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=screenshot_path)

            return {
                "status":          "posted",
                "profile_url":     _FEED_URL,
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
        description="Facebook Poster — AI Employee Gold Tier action script."
    )
    parser.add_argument("--content",      help="Post text")
    parser.add_argument("--content-file", help="Path to file containing post text")
    parser.add_argument(
        "--session-path",
        default=str(_DEFAULT_SESSION),
        help=f"Path to Facebook Playwright session (default: {_DEFAULT_SESSION})",
    )
    parser.add_argument("--headless",    action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--dry-run",     action="store_true")
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
    result = create_post(content=content, session_path=session_path,
                         headless=args.headless, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("posted", "dry_run") else 1)


if __name__ == "__main__":
    main()
