#!/usr/bin/env python3
"""
LinkedIn Poster — AI Employee Gold Tier skill script.

Publishes a post to a LinkedIn Company Page using the official UGC Posts API.
Must only be called after human approval via vault/Approved/.

Setup:
    Add to .env:
        LINKEDIN_API_ACCESS_TOKEN=xxxxx
        LINKEDIN_API_ORG_URN=urn:li:organization:xxxxx

    To get these values run:
        python watchers/auth_linkedin_api.py

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

_UGC_POSTS_URL   = "https://api.linkedin.com/v2/ugcPosts"
_MAX_CONTENT_LEN = 3000  # LinkedIn post character limit


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_credentials() -> tuple[str, str]:
    access_token = os.environ.get("LINKEDIN_API_ACCESS_TOKEN", "")
    # Prefer company page; fall back to personal profile
    author_urn   = (
        os.environ.get("LINKEDIN_API_ORG_URN", "").strip()
        or os.environ.get("LINKEDIN_API_PERSON_URN", "").strip()
    )
    return access_token, author_urn


def create_post(content: str, dry_run: bool = False) -> dict:
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
            "status":      "dry_run",
            "content_len": len(content),
            "preview":     content[:120] + ("..." if len(content) > 120 else ""),
            "timestamp":   _ts(),
        }

    access_token, author_urn = _get_credentials()
    if not access_token or not author_urn:
        return {
            "status":    "error",
            "error":     (
                "Missing LinkedIn credentials. Add LINKEDIN_API_ACCESS_TOKEN and "
                "LINKEDIN_API_ORG_URN (company page) or LINKEDIN_API_PERSON_URN (personal) "
                "to .env. Run: python watchers/auth_linkedin_api.py"
            ),
            "timestamp": _ts(),
        }

    headers = {
        "Authorization":             f"Bearer {access_token}",
        "Content-Type":              "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    body = {
        "author":          author_urn,
        "lifecycleState":  "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary":    {"text": content},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    try:
        resp = requests.post(_UGC_POSTS_URL, headers=headers, json=body, timeout=30)
    except requests.RequestException as exc:
        return {"status": "error", "error": f"Network error: {exc}", "timestamp": _ts()}

    if resp.status_code not in (200, 201):
        try:
            err_body = resp.json()
        except Exception:
            err_body = {"message": resp.text}

        error_msg = err_body.get("message", err_body.get("error_description", "Unknown error"))

        if resp.status_code == 401:
            error_msg += (
                " — Access token expired (~60 days). "
                "Re-run: python watchers/auth_linkedin_api.py"
            )
        elif resp.status_code == 403:
            error_msg += (
                " — Missing w_organization_social scope or user is not an admin of this page. "
                "Re-run: python watchers/auth_linkedin_api.py"
            )

        return {
            "status":      "error",
            "error":       error_msg,
            "http_status": resp.status_code,
            "timestamp":   _ts(),
        }

    post_id  = resp.headers.get("x-restli-id", "")
    post_url = f"https://www.linkedin.com/feed/update/{post_id}/" if post_id else "https://www.linkedin.com/feed/"

    return {
        "status":          "posted",
        "post_id":         post_id,
        "post_url":        post_url,
        "author":          author_urn,
        "content_preview": content[:80] + ("..." if len(content) > 80 else ""),
        "timestamp":       _ts(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="LinkedIn Company Page Poster — UGC Posts API.")
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
