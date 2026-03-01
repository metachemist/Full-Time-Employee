"""Twitter/X one-time authentication helper.

Opens a visible Playwright Chromium window pointed at X.com's login page.
Log in with your X username/email + password (NOT "Continue with Google" —
Google blocks OAuth in automated browsers).

Once you see your X.com home feed and close the window, the session is saved
in Playwright's Chromium format. The twitter-poster skill will reuse it
headlessly without asking you to log in again.

Usage:
    python auth_twitter.py

Why email + password instead of Google Sign-In?
    Google detects Playwright's automation flags and blocks its OAuth flow.
    X.com email/password login goes directly to X's servers so it works
    without any restrictions.
"""

import os
import sys
import shutil
from pathlib import Path
from dotenv import load_dotenv

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
except ImportError:
    sys.exit("Missing dependency: playwright\nRun: pip install playwright && playwright install chromium")

load_dotenv()


def main() -> None:
    session = os.getenv("TWITTER_SESSION_PATH")
    if not session:
        print(
            "ERROR: TWITTER_SESSION_PATH is not set in your .env file.\n"
            "Example: TWITTER_SESSION_PATH=/home/you/.sessions/twitter",
            file=sys.stderr,
        )
        sys.exit(1)

    session_path = Path(session).expanduser().resolve()

    # Wipe any existing session — start clean to avoid stale cookie issues
    if session_path.exists():
        existing = list(session_path.iterdir())
        if existing:
            print(f"Clearing old session data in {session_path} …")
            shutil.rmtree(session_path)

    session_path.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Twitter/X Auth Helper")
    print("=" * 60)
    print(f"Session will be saved to: {session_path}")
    print()
    print("IMPORTANT — use email/username + password to log in.")
    print("Do NOT click 'Continue with Google' (it gets blocked).")
    print()
    print("Steps:")
    print("  1. A browser window will open on X.com's login page.")
    print("  2. Enter your X email/username and password, then sign in.")
    print("  3. If prompted for 2FA or phone verification, complete it.")
    print("  4. Wait until your X home feed fully loads.")
    print("  5. Close the browser window.")
    print()
    print("Opening browser… (this script waits until you close it)")
    print()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1440, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=30_000)

        print("Waiting for you to log in and reach the X home feed…")
        print("(You have 5 minutes)")

        logged_in = False
        for _ in range(150):  # 5 minutes × 2s polls
            try:
                page.wait_for_url(
                    lambda url: "home" in url or ("/i/" not in url and "login" not in url
                                                   and "signin" not in url and "flow" not in url
                                                   and "x.com" in url),
                    timeout=2_000,
                )
                # Extra confirmation: look for the compose button
                page.wait_for_selector(
                    "a[data-testid='SideNav_NewTweet_Button'], button[aria-label='Post']",
                    timeout=2_000,
                )
                logged_in = True
                break
            except PwTimeout:
                # Check if browser was closed by user
                try:
                    _ = page.url
                except Exception:
                    break

        if logged_in:
            print()
            print("Login detected! Saving session… (wait 3 seconds)")
            page.wait_for_timeout(3_000)

        context.close()

    if logged_in:
        saved_files = list(session_path.iterdir())
        print()
        print("Session saved successfully.")
        print(f"  Location  : {session_path}")
        print(f"  Files     : {len(saved_files)} items")
        print()
        print("You can now post tweets via the approval workflow.")
        print("Test with a dry run:")
        print("  python .claude/skills/twitter-poster/scripts/create_post.py \\")
        print(f'    --content "Test" --session-path {session_path} --dry-run')
    else:
        print()
        print("WARNING: Login was not detected or the browser was closed early.")
        print("Re-run this script and complete the full login before closing.")


if __name__ == "__main__":
    main()
