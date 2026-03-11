#!/usr/bin/env python3
"""
LinkedIn API OAuth 2.0 Auth Helper — Company Page edition.

Performs the 3-legged OAuth flow to obtain an access token for posting
to a LinkedIn Company Page. Lists all pages you admin and lets you pick one.
Saves token + org URN to watchers/.state/linkedin_api_token.json.

Prerequisites:
    1. LinkedIn Developer App at https://www.linkedin.com/developers/
    2. Add product: "Share on LinkedIn"
       (grants w_organization_social + r_organization_social scopes)
    3. Set Redirect URL to: http://localhost:8765/callback
    4. Copy Client ID and Client Secret into .env:
           LINKEDIN_API_CLIENT_ID=xxxxx
           LINKEDIN_API_CLIENT_SECRET=xxxxx
    5. Run: python watchers/auth_linkedin_api.py

Scopes requested: w_organization_social r_organization_social
"""

import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

try:
    from dotenv import load_dotenv
    _PROJECT_DIR = Path(__file__).resolve().parents[1]
    load_dotenv(_PROJECT_DIR / ".env")
except ImportError:
    _PROJECT_DIR = Path(__file__).resolve().parent

_STATE_DIR    = Path(__file__).parent / ".state"
_TOKEN_FILE   = _STATE_DIR / "linkedin_api_token.json"
_REDIRECT_URI = "http://localhost:8765/"
_AUTH_URL     = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL    = "https://www.linkedin.com/oauth/v2/accessToken"
_ACLS_URL     = "https://api.linkedin.com/v2/organizationAcls"
_ORG_URL      = "https://api.linkedin.com/v2/organizations"
_SCOPES       = "w_organization_social r_organization_social"


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Local callback server ──────────────────────────────────────────────────────

_received_code:  str | None = None
_received_state: str | None = None
_server_event = threading.Event()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        global _received_code, _received_state
        parsed          = urllib.parse.urlparse(self.path)
        params          = urllib.parse.parse_qs(parsed.query)
        _received_code  = params.get("code",  [None])[0]
        _received_state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authentication successful! You can close this tab.</h2>")
        _server_event.set()

    def log_message(self, format, *args):  # noqa: A002
        pass


def _run_callback_server() -> None:
    server = http.server.HTTPServer(("localhost", 8765), _CallbackHandler)
    server.handle_request()


# ── Token exchange ─────────────────────────────────────────────────────────────

def _exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  _REDIRECT_URI,
            "client_id":     client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Org lookup ─────────────────────────────────────────────────────────────────

def _get_admin_orgs(access_token: str) -> list[dict]:
    """Return list of {id, name, urn} for all orgs the token holder admins."""
    headers = {
        "Authorization":             f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    resp = requests.get(
        _ACLS_URL,
        params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"},
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    orgs = []
    for el in elements:
        org_urn = el.get("organization", "")
        if not org_urn:
            continue
        org_id = org_urn.split(":")[-1]
        # Fetch org name
        try:
            r = requests.get(
                f"{_ORG_URL}/{org_id}",
                params={"fields": "localizedName"},
                headers=headers,
                timeout=10,
            )
            name = r.json().get("localizedName", org_id) if r.status_code == 200 else org_id
        except Exception:
            name = org_id
        orgs.append({"id": org_id, "name": name, "urn": org_urn})

    return orgs


# ── Main flow ──────────────────────────────────────────────────────────────────

def main() -> None:
    client_id     = os.environ.get("LINKEDIN_API_CLIENT_ID", "")
    client_secret = os.environ.get("LINKEDIN_API_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        sys.exit(
            "ERROR: LINKEDIN_API_CLIENT_ID and LINKEDIN_API_CLIENT_SECRET must be set in .env"
        )

    state = secrets.token_urlsafe(16)

    auth_params = {
        "response_type": "code",
        "client_id":     client_id,
        "redirect_uri":  _REDIRECT_URI,
        "state":         state,
        "scope":         _SCOPES,
    }
    auth_url = f"{_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    t = threading.Thread(target=_run_callback_server, daemon=True)
    t.start()

    print(f"\nOpening LinkedIn auth URL in your browser...")
    print(f"If it does not open automatically, paste this URL:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    if not _server_event.wait(timeout=120):
        sys.exit("ERROR: Timed out waiting for browser callback.")

    if _received_state != state:
        sys.exit("ERROR: State mismatch — possible CSRF. Aborting.")

    if not _received_code:
        sys.exit("ERROR: No authorization code received.")

    print("Authorization code received. Exchanging for access token...")
    token_data    = _exchange_code(_received_code, client_id, client_secret)
    access_token  = token_data.get("access_token", "")
    expires_in    = token_data.get("expires_in", 0)
    refresh_token = token_data.get("refresh_token", "")

    if not access_token:
        sys.exit(f"ERROR: No access_token in response: {token_data}")

    print("Fetching company pages you admin...")
    try:
        orgs = _get_admin_orgs(access_token)
    except Exception as exc:
        sys.exit(f"ERROR fetching org list: {exc}")

    if not orgs:
        sys.exit(
            "ERROR: No company pages found where you are an Administrator.\n"
            "Make sure your LinkedIn account is a Page Admin and the app has 'Share on LinkedIn' product."
        )

    # Pick org
    if len(orgs) == 1:
        org = orgs[0]
        print(f"Found 1 company page: {org['name']} ({org['urn']})")
    else:
        print("\nMultiple company pages found. Enter the number to use:")
        for i, o in enumerate(orgs, 1):
            print(f"  {i}. {o['name']}  ({o['urn']})")
        while True:
            choice = input("Choice [1]: ").strip() or "1"
            if choice.isdigit() and 1 <= int(choice) <= len(orgs):
                org = orgs[int(choice) - 1]
                break
            print("  Invalid choice, try again.")

    org_urn = org["urn"]

    # Save state
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "expires_in":    expires_in,
        "org_urn":       org_urn,
        "org_name":      org["name"],
        "obtained_at":   _ts(),
    }, indent=2))
    print(f"\nToken saved to: {_TOKEN_FILE}")

    print("\n" + "=" * 60)
    print("Add these lines to your .env file:")
    print("=" * 60)
    print(f"LINKEDIN_API_ACCESS_TOKEN={access_token}")
    print(f"LINKEDIN_API_ORG_URN={org_urn}")
    print("=" * 60)
    print(f"\nPosting as: {org['name']}")
    print(f"Token expires in {expires_in // 86400} days.")
    if refresh_token:
        print("Refresh token saved — re-run this script when token expires.")
    print()


if __name__ == "__main__":
    main()
