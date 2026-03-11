#!/usr/bin/env python3
"""
Instagram Poster — AI Employee Gold Tier skill script.

Publishes a photo post with caption to Instagram using the official
Content Publishing API (no browser required).

Requirements:
    - Instagram Business or Creator account linked to a Facebook Page
    - Image must be at a publicly accessible HTTPS URL

Setup:
    Add to .env:
        INSTAGRAM_USER_ID=123456789          # IG Business Account ID
        INSTAGRAM_ACCESS_TOKEN=EAAxxxxx      # Page or user token with instagram_content_publish
        # Optional: auto-derived from FACEBOOK_ACCESS_TOKEN + FACEBOOK_PAGE_ID if absent:
        # FACEBOOK_ACCESS_TOKEN=EAAxxxxx
        # FACEBOOK_PAGE_ID=123456789

Usage:
    python create_post.py --caption "Caption..." --image-url "https://example.com/image.jpg"
    python create_post.py --caption "Caption..." --image-url "https://..." --dry-run
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

try:
    from dotenv import load_dotenv
    _PROJECT_DIR = Path(__file__).resolve().parents[4]
    load_dotenv(_PROJECT_DIR / ".env")
except ImportError:
    pass

_GRAPH_API_VERSION = "v21.0"
_GRAPH_API_BASE    = f"https://graph.facebook.com/{_GRAPH_API_VERSION}"
_MAX_CAPTION_LEN   = 2200
_PUBLISH_TIMEOUT_S = 90   # max seconds to wait for media container to be ready


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_credentials() -> tuple[str, str]:
    """
    Returns (instagram_user_id, access_token).
    Priority 1: INSTAGRAM_USER_ID + INSTAGRAM_ACCESS_TOKEN (direct).
    Priority 2: Derive IG User ID from FACEBOOK_PAGE_ID via graph API.
    """
    ig_user_id   = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    access_token = (
        os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
        or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
        or os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()
    )

    if ig_user_id and access_token:
        return ig_user_id, access_token

    if not access_token:
        return "", ""

    # Auto-derive IG User ID from the Facebook Page
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
    if not page_id:
        return "", ""

    resp = requests.get(
        f"{_GRAPH_API_BASE}/{page_id}",
        params={"fields": "instagram_business_account", "access_token": access_token},
        timeout=15,
    )
    if resp.status_code == 200:
        ig_account = resp.json().get("instagram_business_account", {})
        ig_user_id = ig_account.get("id", "")

    return ig_user_id, access_token


def _resolve_image_url(url: str) -> str:
    """Follow redirects and return the final URL (Instagram API requires direct links)."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10)
        return resp.url
    except Exception:
        return url  # fall back to original if HEAD fails


def _wait_for_container(creation_id: str, access_token: str) -> bool:
    """Poll until the media container status is FINISHED or timeout."""
    deadline = time.time() + _PUBLISH_TIMEOUT_S
    while time.time() < deadline:
        resp = requests.get(
            f"{_GRAPH_API_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=15,
        )
        if resp.status_code == 200:
            status = resp.json().get("status_code", "")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                return False
        time.sleep(4)
    return False


def create_post(caption: str, image_url: str, dry_run: bool = False) -> dict:
    if not caption.strip():
        return {"status": "error", "error": "Caption is empty.", "timestamp": _ts()}

    if len(caption) > _MAX_CAPTION_LEN:
        return {
            "status":    "error",
            "error":     f"Caption exceeds Instagram limit ({len(caption)}/{_MAX_CAPTION_LEN} chars).",
            "timestamp": _ts(),
        }

    if not image_url.startswith("https://"):
        return {
            "status":    "error",
            "error":     "image_url must be a public HTTPS URL (Instagram API requirement).",
            "timestamp": _ts(),
        }

    if dry_run:
        return {
            "status":      "dry_run",
            "caption_len": len(caption),
            "image_url":   image_url,
            "preview":     caption[:120] + ("..." if len(caption) > 120 else ""),
            "timestamp":   _ts(),
        }

    ig_user_id, access_token = _get_credentials()
    if not ig_user_id or not access_token:
        return {
            "status":    "error",
            "error":     (
                "Missing Instagram credentials. Set INSTAGRAM_USER_ID + INSTAGRAM_ACCESS_TOKEN "
                "in .env (or FACEBOOK_PAGE_ID + FACEBOOK_ACCESS_TOKEN for auto-detection). "
                "See SKILL.md for setup."
            ),
            "timestamp": _ts(),
        }

    # Resolve any redirects — Instagram API requires a direct (non-redirect) URL
    image_url = _resolve_image_url(image_url)

    # Step 1: Create media container
    try:
        resp = requests.post(
            f"{_GRAPH_API_BASE}/{ig_user_id}/media",
            data={
                "image_url":    image_url,
                "caption":      caption,
                "access_token": access_token,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"status": "error", "error": f"Network error (create container): {exc}", "timestamp": _ts()}

    if resp.status_code not in (200, 201):
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {"message": resp.text}
        error_msg = err.get("message", "Unknown error")
        if resp.status_code == 401:
            error_msg += " — Token expired. Re-run watchers/auth_facebook.py or refresh INSTAGRAM_ACCESS_TOKEN."
        return {"status": "error", "error": error_msg, "http_status": resp.status_code, "timestamp": _ts()}

    creation_id = resp.json().get("id", "")
    if not creation_id:
        return {"status": "error", "error": "No creation_id returned from /media endpoint.", "timestamp": _ts()}

    # Step 2: Wait for container to be ready
    if not _wait_for_container(creation_id, access_token):
        return {
            "status":    "error",
            "error":     f"Media container {creation_id} did not reach FINISHED state within {_PUBLISH_TIMEOUT_S}s.",
            "timestamp": _ts(),
        }

    # Step 3: Publish
    try:
        resp = requests.post(
            f"{_GRAPH_API_BASE}/{ig_user_id}/media_publish",
            data={"creation_id": creation_id, "access_token": access_token},
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"status": "error", "error": f"Network error (publish): {exc}", "timestamp": _ts()}

    if resp.status_code not in (200, 201):
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {"message": resp.text}
        return {
            "status":     "error",
            "error":      err.get("message", "Unknown error"),
            "http_status": resp.status_code,
            "timestamp":  _ts(),
        }

    post_id  = resp.json().get("id", "")
    post_url = f"https://www.instagram.com/p/{post_id}/" if post_id else "https://www.instagram.com/"

    return {
        "status":          "posted",
        "post_id":         post_id,
        "url":             post_url,
        "image_url":       image_url,
        "caption_preview": caption[:80] + ("..." if len(caption) > 80 else ""),
        "timestamp":       _ts(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Instagram Poster — Content Publishing API action script.")
    parser.add_argument("--caption",    required=True, help="Post caption (max 2200 chars)")
    parser.add_argument("--image-url",  required=True, help="Public HTTPS URL of the image to post")
    parser.add_argument("--dry-run",    action="store_true", help="Preview without posting")
    args = parser.parse_args()

    result = create_post(
        caption   = args.caption,
        image_url = args.image_url,
        dry_run   = args.dry_run,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("posted", "dry_run") else 1)


if __name__ == "__main__":
    main()
