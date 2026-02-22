"""LinkedIn selector diagnostics — runs headlessly using the saved session."""
import os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
except ImportError:
    sys.exit("pip install playwright && playwright install chromium")

SCRIPT_DIR = Path(__file__).parent
session = os.getenv("LINKEDIN_SESSION_PATH")
if not session:
    sys.exit("LINKEDIN_SESSION_PATH not set")

session_path = Path(session).expanduser().resolve()

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(session_path),
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
        viewport={"width": 1440, "height": 900},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # ── MESSAGING PAGE ────────────────────────────────────────────────────
    print("\n=== MESSAGING PAGE ===")
    page.goto("https://www.linkedin.com/messaging/", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(4_000)
    page.screenshot(path=str(SCRIPT_DIR / "debug_linkedin_messaging.png"))
    print(f"Screenshot → debug_linkedin_messaging.png")
    print(f"URL: {page.url}")

    msg_probes = {
        "li.msg-conversation-listitem":       "li.msg-conversation-listitem",
        "li[class*=conversation]":             "li[class*='conversation']",
        "div[class*=conversation]":            "div[class*='conversation']",
        "ul[class*=conversation]":             "ul[class*='conversation']",
        "[data-control-name*=conversation]":   "[data-control-name*='conversation']",
        "a[href*='/messaging/thread/']":       "a[href*='/messaging/thread/']",
        "div[class*=msg-]":                    "div[class*='msg-']",
        "li[class*=msg-]":                     "li[class*='msg-']",
    }
    for name, sel in msg_probes.items():
        els = page.query_selector_all(sel)
        if els:
            sample = ""
            try: sample = f'  → "{els[0].inner_text()[:60].strip()}"'
            except: pass
            print(f"  FOUND {len(els):3d}  {name}{sample}")
        else:
            print(f"        0    {name}")

    # Save messaging HTML
    msg_html = page.content()
    (SCRIPT_DIR / "debug_linkedin_messaging.html").write_text(msg_html, encoding="utf-8")
    print(f"Full page HTML → debug_linkedin_messaging.html ({len(msg_html):,} chars)")

    # ── INVITATIONS PAGE ─────────────────────────────────────────────────
    print("\n=== INVITATIONS PAGE ===")
    page.goto("https://www.linkedin.com/mynetwork/invitation-manager/", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(4_000)
    page.screenshot(path=str(SCRIPT_DIR / "debug_linkedin_invitations.png"))
    print(f"Screenshot → debug_linkedin_invitations.png")
    print(f"URL: {page.url}")

    inv_probes = {
        "ul.invitation-list":                  "ul.invitation-list",
        "ul[aria-label*='Invitation']":        "ul[aria-label*='Invitation']",
        "li.invitation-card":                  "li.invitation-card",
        "li[class*=invitation]":               "li[class*='invitation']",
        "div[class*=invitation]":              "div[class*='invitation']",
        "[data-control-name*=invite]":         "[data-control-name*='invite']",
        "section[class*=invitation]":          "section[class*='invitation']",
        "div[class*=mn-invitation]":           "div[class*='mn-invitation']",
        "ul[class*=invit]":                    "ul[class*='invit']",
    }
    for name, sel in inv_probes.items():
        els = page.query_selector_all(sel)
        if els:
            sample = ""
            try: sample = f'  → "{els[0].inner_text()[:60].strip()}"'
            except: pass
            print(f"  FOUND {len(els):3d}  {name}{sample}")
        else:
            print(f"        0    {name}")

    inv_html = page.content()
    (SCRIPT_DIR / "debug_linkedin_invitations.html").write_text(inv_html, encoding="utf-8")
    print(f"Full page HTML → debug_linkedin_invitations.html ({len(inv_html):,} chars)")

    ctx.close()

print("\nDone.")
