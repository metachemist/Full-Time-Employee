"""Twitter/X one-time authentication helper.

Opens a Playwright Chromium window for manual X.com login.
Once logged in and the feed loads, close the window — session is saved.

Usage:
    python auth_twitter.py

X.com login is a 3-step flow:
    Step 1: Enter email or phone number → click Next
    Step 2: Enter your @username (identity verification) → click Next
    Step 3: Enter password → click Log in
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
    print("X.com LOGIN IS A 3-STEP FLOW:")
    print()
    print("  Step 1 → Enter your email or phone number, click Next")
    print("  Step 2 → Enter your @username (without @), click Next")
    print("           X always asks this to verify identity.")
    print("           It is NOT asking for your password yet.")
    print("  Step 3 → Enter your password, click Log in")
    print()
    print("Do NOT use 'Continue with Google' — it gets blocked.")
    print()
    print("Opening browser… complete all 3 steps, then wait for")
    print("your home feed to load. Close the browser when done.")
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
        page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=30_000)

        logged_in = False
        while True:
            try:
                current_url = page.url
                if "x.com/home" in current_url:
                    # Feed loaded — give it 3 seconds to finish writing cookies
                    page.wait_for_timeout(3_000)
                    logged_in = True
                    break
                page.wait_for_timeout(2_000)
            except Exception:
                # Browser was closed by user
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
        print("  .venv/bin/python .claude/skills/twitter-poster/scripts/create_post.py \\")
        print(f'    --content "Test" --session-path {session_path} --dry-run')
    else:
        print()
        print("Session NOT saved — browser was closed before reaching x.com/home.")
        print("Re-run and complete all 3 login steps before closing the browser.")


if __name__ == "__main__":
    main()
