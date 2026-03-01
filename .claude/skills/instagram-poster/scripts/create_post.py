#!/usr/bin/env python3
"""
Instagram Poster — AI Employee Gold Tier skill script.

Publishes a photo post with caption to Instagram using a persistent
Playwright session. Must only be called after human approval via vault/Approved/.

Instagram requires an image — text-only posts are not supported on the web.

Usage:
    python create_post.py --caption "Caption..." --image-path /path/to/image.jpg --session-path ~/.sessions/instagram
    python create_post.py --caption "Caption..." --image-path /path/to/image.jpg --dry-run
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

_DEFAULT_SESSION = Path(os.environ.get("INSTAGRAM_SESSION_PATH", "~/.sessions/instagram")).expanduser()
_HOME_URL        = "https://www.instagram.com/"
_MAX_CAPTION_LEN = 2200  # Instagram caption character limit


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_post(
    caption: str,
    image_path: Path,
    session_path: Path,
    headless: bool = True,
    dry_run: bool = False,
) -> dict:
    if not caption.strip():
        return {"status": "error", "error": "Caption is empty.", "timestamp": _ts()}

    if len(caption) > _MAX_CAPTION_LEN:
        return {
            "status":    "error",
            "error":     f"Caption exceeds Instagram limit ({len(caption)}/{_MAX_CAPTION_LEN} chars).",
            "timestamp": _ts(),
        }

    if not image_path.exists():
        return {
            "status":    "error",
            "error":     f"Image file not found: {image_path}",
            "timestamp": _ts(),
        }

    if dry_run:
        return {
            "status":       "dry_run",
            "caption_len":  len(caption),
            "image":        str(image_path),
            "preview":      caption[:120] + ("..." if len(caption) > 120 else ""),
            "timestamp":    _ts(),
        }

    if not session_path.exists():
        return {
            "status":    "error",
            "error":     f"Instagram session not found: {session_path}. Run: python watchers/auth_instagram.py",
            "timestamp": _ts(),
        }

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(session_path),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            # ── Navigate to home ──────────────────────────────────────────
            page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)

            # ── Check login ───────────────────────────────────────────────
            if "login" in page.url or "accounts" in page.url:
                return {
                    "status":    "error",
                    "error":     "Instagram session expired. Run: python watchers/auth_instagram.py",
                    "timestamp": _ts(),
                }

            # ── Click the Create / New Post button ────────────────────────
            create_selectors = [
                "a[href='/create/select/']",
                "svg[aria-label='New post']",
                "[aria-label='New post']",
                "a[aria-label='New post']",
            ]
            clicked = False
            for sel in create_selectors:
                try:
                    page.click(sel, timeout=5000)
                    clicked = True
                    break
                except PlaywrightTimeout:
                    continue

            if not clicked:
                # Try clicking via JS — Instagram's nav icons are SVG-heavy
                clicked = page.evaluate("""
                    () => {
                        const links = [...document.querySelectorAll('a')];
                        const create = links.find(a => a.href && a.href.includes('/create/'));
                        if (create) { create.click(); return true; }
                        return false;
                    }
                """)

            if not clicked:
                screenshot_path = f"/tmp/instagram_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not find Create button. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(2000)

            # ── Upload image via the file input ───────────────────────────
            # Instagram's create flow shows a file picker dialog
            file_input_sel = "input[type='file']"
            try:
                page.wait_for_selector(file_input_sel, timeout=8000)
                page.set_input_files(file_input_sel, str(image_path.resolve()))
            except PlaywrightTimeout:
                screenshot_path = f"/tmp/instagram_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"File input not found. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(2000)

            # ── Click Next through crop and filter steps ──────────────────
            for step_label in ("Next", "Next"):
                next_selectors = [
                    "button:has-text('Next')",
                    "[aria-label='Next']",
                    "div[role='button']:has-text('Next')",
                ]
                for sel in next_selectors:
                    try:
                        page.click(sel, timeout=5000)
                        break
                    except PlaywrightTimeout:
                        continue
                page.wait_for_timeout(1500)

            # ── Type caption ──────────────────────────────────────────────
            caption_selectors = [
                "div[aria-label='Write a caption...']",
                "textarea[aria-label*='caption']",
                "div[role='textbox'][aria-label*='caption']",
                "div[contenteditable='true']",
            ]
            typed = False
            for sel in caption_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.keyboard.type(caption, delay=20)
                    typed = True
                    break
                except PlaywrightTimeout:
                    continue

            if not typed:
                screenshot_path = f"/tmp/instagram_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not find caption field. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            page.wait_for_timeout(1000)

            # ── Click Share ───────────────────────────────────────────────
            share_selectors = [
                "button:has-text('Share')",
                "div[role='button']:has-text('Share')",
                "[aria-label='Share']",
            ]
            shared = False
            for sel in share_selectors:
                try:
                    page.click(sel, timeout=5000)
                    shared = True
                    break
                except PlaywrightTimeout:
                    continue

            if not shared:
                screenshot_path = f"/tmp/instagram_post_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                page.screenshot(path=screenshot_path)
                return {
                    "status":    "error",
                    "error":     f"Could not click Share button. Screenshot: {screenshot_path}",
                    "timestamp": _ts(),
                }

            # ── Wait for upload and confirmation ──────────────────────────
            page.wait_for_timeout(5000)

            screenshot_path = f"/tmp/instagram_post_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            page.screenshot(path=screenshot_path)

            return {
                "status":          "posted",
                "profile_url":     _HOME_URL,
                "screenshot":      screenshot_path,
                "caption_preview": caption[:80] + ("..." if len(caption) > 80 else ""),
                "image":           str(image_path),
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
        description="Instagram Poster — AI Employee Gold Tier action script."
    )
    parser.add_argument("--caption",      required=True, help="Post caption (max 2200 chars)")
    parser.add_argument("--image-path",   required=True, help="Path to image file (JPG/PNG)")
    parser.add_argument(
        "--session-path",
        default=str(_DEFAULT_SESSION),
        help=f"Path to Instagram Playwright session (default: {_DEFAULT_SESSION})",
    )
    parser.add_argument("--headless",    action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()

    image_path   = Path(args.image_path).expanduser().resolve()
    session_path = Path(args.session_path).expanduser().resolve()

    result = create_post(
        caption      = args.caption,
        image_path   = image_path,
        session_path = session_path,
        headless     = args.headless,
        dry_run      = args.dry_run,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("posted", "dry_run") else 1)


if __name__ == "__main__":
    main()
