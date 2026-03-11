#!/usr/bin/env python3
"""
Facebook Poster — AI Employee Gold Tier skill script.

Publishes a text post to a Facebook Page using the Graph API.
Must only be called after human approval via vault/Approved/.

Credential resolution (in order):
    1. FACEBOOK_PAGE_ACCESS_TOKEN set directly in .env → used as-is
    2. FACEBOOK_ACCESS_TOKEN + FACEBOOK_PAGE_ID → page token fetched via /me/accounts

Setup:
    Add to .env:
        FACEBOOK_APP_ID=xxxxx
        FACEBOOK_APP_SECRET=xxxxx
        FACEBOOK_ACCESS_TOKEN=EAAxxxxx   # user-level token with pages_manage_posts
        FACEBOOK_PAGE_ID=123456789
        # Optional (auto-derived if absent):
        FACEBOOK_PAGE_ACCESS_TOKEN=EAAxxxxx

Usage:
    python create_post.py --content "Post text..."
    python create_post.py --content-file /tmp/post.txt
    python create_post.py --content "Post text..." --dry-run
"""

import argparse
import json
import os
import sys
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
_MAX_CONTENT_LEN   = 63206


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_page_access_token() -> tuple[str, str]:
    """
    Returns (page_id, page_access_token).
    Priority 1: FACEBOOK_PAGE_ACCESS_TOKEN + FACEBOOK_PAGE_ID (direct).
    Priority 2: Derive page token from FACEBOOK_ACCESS_TOKEN via /me/accounts.
    """
    page_id       = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
    page_token    = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
    user_token    = os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()

    if page_token and page_id:
        return page_id, page_token

    if not user_token:
        return "", ""

    # Strategy 1: derive page token from /me/accounts
    resp = requests.get(
        f"{_GRAPH_API_BASE}/me/accounts",
        params={"access_token": user_token, "fields": "id,name,access_token"},
        timeout=15,
    )
    if resp.status_code == 200:
        for page in resp.json().get("data", []):
            if not page_id or page.get("id") == page_id:
                return page["id"], page["access_token"]

    # Strategy 2: query the page directly (works for new-style pages)
    if page_id:
        resp2 = requests.get(
            f"{_GRAPH_API_BASE}/{page_id}",
            params={"fields": "id,name,access_token", "access_token": user_token},
            timeout=15,
        )
        if resp2.status_code == 200:
            data = resp2.json()
            derived = data.get("access_token", "")
            if derived:
                return page_id, derived

    return "", ""


def create_post(content: str, dry_run: bool = False) -> dict:
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
            "status":      "dry_run",
            "content_len": len(content),
            "preview":     content[:120] + ("..." if len(content) > 120 else ""),
            "timestamp":   _ts(),
        }

    page_id, page_token = _get_page_access_token()
    if not page_id or not page_token:
        return {
            "status":    "error",
            "error":     (
                "Missing Facebook credentials. Set FACEBOOK_PAGE_ACCESS_TOKEN + FACEBOOK_PAGE_ID, "
                "or FACEBOOK_ACCESS_TOKEN + FACEBOOK_PAGE_ID in .env. See SKILL.md for setup."
            ),
            "timestamp": _ts(),
        }

    try:
        resp = requests.post(
            f"{_GRAPH_API_BASE}/{page_id}/feed",
            data={"message": content, "access_token": page_token},
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"status": "error", "error": f"Network error: {exc}", "timestamp": _ts()}

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {"message": resp.text}

        error_msg  = err.get("message", "Unknown error")
        error_code = err.get("code", resp.status_code)

        if error_code in (190, 102):
            error_msg += (
                " — Token expired. Refresh: unset FACEBOOK_PAGE_ACCESS_TOKEN in .env "
                "so the script auto-fetches a new one from FACEBOOK_ACCESS_TOKEN."
            )

        return {
            "status":     "error",
            "error":      error_msg,
            "error_code": error_code,
            "timestamp":  _ts(),
        }

    post_id  = resp.json().get("id", "")
    post_url = f"https://www.facebook.com/{post_id}" if post_id else f"https://www.facebook.com/{page_id}"

    return {
        "status":          "posted",
        "post_id":         post_id,
        "url":             post_url,
        "content_preview": content[:80] + ("..." if len(content) > 80 else ""),
        "timestamp":       _ts(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Facebook Poster — Graph API action script.")
    parser.add_argument("--content",      help="Post text")
    parser.add_argument("--content-file", help="Path to file containing post text")
    parser.add_argument("--dry-run",      action="store_true", help="Preview without posting")
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

    result = create_post(content=content, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("posted", "dry_run") else 1)


if __name__ == "__main__":
    main()
