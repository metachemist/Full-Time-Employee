"""Facebook one-time authentication helper.

Opens a Playwright Chromium window for manual Facebook login.
Once logged in and the feed loads, close the window — session is saved.

Usage:
    python auth_facebook.py
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
    session = os.getenv("FACEBOOK_SESSION_PATH")
    if not session:
        print(
            "ERROR: FACEBOOK_SESSION_PATH is not set in your .env file.\n"
            "Example: FACEBOOK_SESSION_PATH=/home/you/.sessions/facebook",
            file=sys.stderr,
        )
        sys.exit(1)

    session_path = Path(session).expanduser().resolve()

    if session_path.exists():
        existing = list(session_path.iterdir())
        if existing:
            print(f"Clearing old session data in {session_path} …")
            shutil.rmtree(session_path)

    session_path.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Facebook Auth Helper")
    print("=" * 60)
    print(f"Session will be saved to: {session_path}")
    print()
    print("Steps:")
    print("  1. A browser window will open on Facebook's login page.")
    print("  2. Enter your email/phone and password, then click Log in.")
    print("  3. Complete any 2FA or identity check if prompted.")
    print("  4. Wait until your Facebook home feed fully loads.")
    print("  5. Close the browser window.")
    print()
    print("Do NOT use 'Continue with Google' — it gets blocked.")
    print("You have 10 minutes.")
    print()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=30_000)

        print("Opening browser… log in, wait for your feed, then close the window.")

        logged_in = False
        while True:
            try:
                url = page.url
                # Logged in when on feed or home, not on login/checkpoint pages
                if "facebook.com" in url and not any(
                    kw in url for kw in ("login", "checkpoint", "recover", "two_step")
                ) and url not in ("https://www.facebook.com/", "https://facebook.com/"):
                    page.wait_for_timeout(3_000)
                    logged_in = True
                    break
                # Also accept the home feed URL directly
                if url in ("https://www.facebook.com/", "https://facebook.com/"):
                    try:
                        page.wait_for_selector(
                            "[aria-label='Create a post'], [aria-placeholder*='on your mind']",
                            timeout=4_000,
                        )
                        page.wait_for_timeout(2_000)
                        logged_in = True
                        break
                    except PwTimeout:
                        pass
                page.wait_for_timeout(2_000)
            except Exception:
                break

        try:
            context.close()
        except Exception:
            pass

    if logged_in:
        saved_files = list(session_path.iterdir())
        print()
        print("Session saved successfully.")
        print(f"  Location : {session_path}")
        print(f"  Files    : {len(saved_files)} items")
        print()
        print("Test with a dry run:")
        print("  .venv/bin/python .claude/skills/facebook-poster/scripts/create_post.py \\")
        print(f'    --content "Test" --session-path {session_path} --dry-run')
    else:
        print()
        print("Session NOT saved — browser was closed before login was detected.")
        print("Re-run and wait for your feed to load before closing.")


if __name__ == "__main__":
    main()
