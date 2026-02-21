"""LinkedIn one-time authentication helper.

Opens a visible Playwright Chromium window pointed at LinkedIn's login page.
Log in with your LinkedIn email + password (NOT "Continue with Google" —
Google blocks OAuth in automated browsers).

Once you see your LinkedIn feed and close the window, the session is saved
in Playwright's Chromium format. linkedin_watcher.py will reuse it headlessly
without asking you to log in again.

Usage:
    python auth_linkedin.py

Why email + password instead of Google Sign-In?
    Google detects Playwright's automation flags and blocks its OAuth flow.
    LinkedIn email/password login goes directly to LinkedIn's own servers
    so it works without any restrictions.

    If you don't know your LinkedIn password, go to:
        https://www.linkedin.com/uas/request-password-reset
    and set one — your account can then be used with both Google and password.
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
    session = os.getenv("LINKEDIN_SESSION_PATH")
    if not session:
        print(
            "ERROR: LINKEDIN_SESSION_PATH is not set in your .env file.\n"
            "Example: LINKEDIN_SESSION_PATH=/home/you/.sessions/linkedin",
            file=sys.stderr,
        )
        sys.exit(1)

    session_path = Path(session).expanduser().resolve()

    # Wipe any existing Chrome-format session — it's incompatible with Chromium
    if session_path.exists():
        existing = list(session_path.iterdir())
        if existing:
            print(f"Clearing old session data in {session_path} …")
            shutil.rmtree(session_path)

    session_path.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("LinkedIn Auth Helper")
    print("=" * 60)
    print(f"Session will be saved to: {session_path}")
    print()
    print("IMPORTANT — use email + password to log in.")
    print("Do NOT click 'Continue with Google' (it gets blocked).")
    print()
    print("Steps:")
    print("  1. A browser window will open on LinkedIn's login page.")
    print("  2. Enter your LinkedIn email and password, then sign in.")
    print("     (No Google needed — just LinkedIn credentials.)")
    print("  3. If prompted for 2FA, complete it.")
    print("  4. Wait until your LinkedIn feed fully loads.")
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
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30_000)

        print("Waiting for you to log in and reach the LinkedIn feed…")
        print("(You have 5 minutes)")

        logged_in = False
        for _ in range(150):  # 5 minutes
            try:
                page.wait_for_url(
                    lambda url: any(
                        kw in url for kw in ("feed", "mynetwork", "messaging", "jobs")
                    ),
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
            page.wait_for_timeout(3_000)  # let LinkedIn finish writing cookies

        context.close()

    if logged_in:
        saved_files = list(session_path.iterdir())
        print()
        print("Session saved successfully.")
        print(f"  Location  : {session_path}")
        print(f"  Files     : {len(saved_files)} items")
        print()
        print("You can now run the watcher headlessly:")
        print("  python linkedin_watcher.py ../vault")
    else:
        print()
        print("WARNING: Login was not detected or the browser was closed early.")
        print("Re-run this script and complete the full login before closing.")


if __name__ == "__main__":
    main()
