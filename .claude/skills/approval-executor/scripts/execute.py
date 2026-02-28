#!/usr/bin/env python3
"""
Approval Executor — AI Employee Silver Tier skill script.

Scans vault/Approved/ for pending APPROVAL_*.md files, parses each one,
dispatches to the appropriate sender script, then moves to vault/Done/
and writes the audit log.

Usage:
    python execute.py --vault ./vault             # execute all approvals
    python execute.py --vault ./vault --dry-run   # preview without executing
    python execute.py --vault ./vault --once-file APPROVAL_SEND_EMAIL_...md
    python execute.py --vault ./vault --loop      # daemon mode (PM2 / cron alternative)
    python execute.py --vault ./vault --loop --interval 60
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pyyaml\nRun: pip install pyyaml")

_SCRIPT_DIR  = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent.parent.parent.parent  # project root

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_DIR / ".env")
except ImportError:
    pass  # dotenv optional — env vars may already be set by PM2 env_file

# ---------------------------------------------------------------------------
# Rate limiting — max actions per hour (in-memory, resets on restart)
# ---------------------------------------------------------------------------

_MAX_ACTIONS_PER_HOUR = 10
_rate_state: dict = {"bucket": None, "count": 0}


def _rate_limit_check() -> bool:
    """Return True if action is allowed; False if hourly cap reached."""
    bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    if _rate_state["bucket"] != bucket:
        _rate_state["bucket"] = bucket
        _rate_state["count"] = 0
    if _rate_state["count"] >= _MAX_ACTIONS_PER_HOUR:
        return False
    _rate_state["count"] += 1
    return True


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Frontmatter + body parser
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def _parse_approval(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        return fm, m.group(2).strip()
    return {}, text.strip()


def _extract_field(body: str, label: str) -> str:
    """Extract value from '- **Label:** value' lines in body."""
    m = re.search(rf"\*\*{re.escape(label)}:?\*\*[:\s]+(.+)", body)
    return m.group(1).strip() if m else ""


def _extract_message(body: str) -> str:
    """Extract content under '## Message / Content' section, stripping 2-space indent."""
    m = re.search(r"##\s+Message[^#\n]*\n+(.*?)(?:\n##|$)", body, re.DOTALL)
    if not m:
        return ""
    raw = m.group(1)
    # Strip consistent leading whitespace (approval builder adds 2 spaces)
    return textwrap.dedent(raw).strip()


# ---------------------------------------------------------------------------
# Skill script paths
# ---------------------------------------------------------------------------

_SKILLS_DIR = _PROJECT_DIR / ".claude" / "skills"

_ACTION_SCRIPTS: dict[str, Path] = {
    "send_email":                    _SKILLS_DIR / "gmail-sender"    / "scripts" / "send_email.py",
    "send_linkedin_post":            _SKILLS_DIR / "linkedin-poster" / "scripts" / "create_post.py",
}


# ---------------------------------------------------------------------------
# Per-action argument builders
# ---------------------------------------------------------------------------

def _build_args(action: str, body: str) -> list[str] | None:
    target  = _extract_field(body, "Target")
    subject = _extract_field(body, "Subject / Title") or _extract_field(body, "Subject")
    message = _extract_message(body)

    if action == "send_email":
        if not target or not message:
            return None
        args = ["--to", target, "--subject", subject or "(no subject)", "--body", message]
        return args

    if action == "send_linkedin_post":
        if not message:
            return None
        args = ["--content", message]
        session = os.environ.get("LINKEDIN_SESSION_PATH", "")
        if session:
            args += ["--session-path", session]
        return args

    return None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def _audit(vault: Path, event: str, **kwargs) -> None:
    entry = {"timestamp": _ts(), "event": event, **kwargs}
    log_file = vault / "Logs" / f"{_today()}.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Single-file executor
# ---------------------------------------------------------------------------

def execute_approval(
    approval_file: Path,
    vault: Path,
    dry_run: bool = False,
) -> dict:
    text = approval_file.read_text(encoding="utf-8")
    fm, body = _parse_approval(text)

    action = str(fm.get("action", "")).lower()
    status = str(fm.get("status", "")).lower()

    if status in ("sent", "posted", "failed"):
        return {"status": "skipped", "reason": f"Already processed: status={status}", "file": approval_file.name}

    # ── Expiry check ──────────────────────────────────────────────────────
    expires_at_raw = fm.get("expires_at")
    if expires_at_raw and not dry_run:
        try:
            exp_dt = datetime.fromisoformat(str(expires_at_raw))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp_dt:
                print(f"  ⚠ Expired at {expires_at_raw} — skipping. Move to Rejected/ to clean up.")
                return {"status": "skipped", "reason": f"Approval expired at {expires_at_raw}", "file": approval_file.name}
        except ValueError:
            pass

    if not action:
        return {"status": "error", "reason": "No 'action' field in frontmatter.", "file": approval_file.name}

    script = _ACTION_SCRIPTS.get(action)
    if not script:
        return {"status": "error", "reason": f"Unknown action type: '{action}'", "file": approval_file.name}

    if not script.exists():
        return {"status": "error", "reason": f"Skill script not found: {script}", "file": approval_file.name}

    extra_args = _build_args(action, body)
    if extra_args is None:
        return {"status": "error", "reason": "Could not extract required fields (target/message) from approval file.", "file": approval_file.name}

    cmd = [sys.executable, str(script)] + extra_args
    if dry_run:
        cmd.append("--dry-run")

    print(f"[{action.upper():<36}] {approval_file.name}")
    if dry_run:
        print(f"  DRY-RUN cmd: {' '.join(cmd[:6])}...")
        _audit(vault, "dry_run", action=action, file=approval_file.name)
        return {"status": "dry_run", "action": action, "file": approval_file.name}

    # ── Rate limit ────────────────────────────────────────────────────────
    if not _rate_limit_check():
        msg = f"Rate limit reached ({_MAX_ACTIONS_PER_HOUR}/hr). Will retry next hour."
        print(f"  ⚠ {msg}")
        _audit(vault, "rate_limited", action=action, file=approval_file.name, limit=_MAX_ACTIONS_PER_HOUR)
        return {"status": "skipped", "reason": msg, "file": approval_file.name}

    # ── Execute ──────────────────────────────────────────────────────────
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output_raw = result.stdout.strip()
        try:
            output = json.loads(output_raw)
        except json.JSONDecodeError:
            output = {"raw": output_raw}

        success = result.returncode == 0 and output.get("status") not in ("error", "failed")
        new_status = output.get("status", "sent" if success else "failed")

        # ── Update approval file ──────────────────────────────────────────
        updated_text = re.sub(r"^status:\s*.+$", f"status: {new_status}", text, flags=re.MULTILINE)
        updated_text += f"\n<!-- executed_at: {_ts()} -->\n"
        approval_file.write_text(updated_text, encoding="utf-8")

        # ── Move to Done ──────────────────────────────────────────────────
        done_dest = vault / "Done" / approval_file.name
        if done_dest.exists():
            done_dest = done_dest.with_name(f"{done_dest.stem}_{int(datetime.now().timestamp())}.md")
        approval_file.rename(done_dest)

        # ── Audit ─────────────────────────────────────────────────────────
        _audit(vault, "action_executed", action=action, file=approval_file.name,
               result="success" if success else "error",
               output=output, returncode=result.returncode)

        status_icon = "✓" if success else "✗"
        print(f"  {status_icon} {new_status.upper()}")
        if not success and result.stderr:
            print(f"  stderr: {result.stderr[:200]}")

        return {
            "status": "success" if success else "error",
            "action": action,
            "file":   approval_file.name,
            "result": output,
        }

    except subprocess.TimeoutExpired:
        _audit(vault, "action_timeout", action=action, file=approval_file.name)
        return {"status": "error", "reason": "Script timed out after 120 s.", "file": approval_file.name}
    except Exception as exc:
        _audit(vault, "action_error", action=action, file=approval_file.name, error=str(exc))
        return {"status": "error", "reason": str(exc), "file": approval_file.name}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_once(vault: Path, approved_dir: Path, dry_run: bool, once_file: str | None) -> int:
    """Process one batch of approvals. Returns exit code (0=ok, 1=errors)."""
    if once_file:
        files = [approved_dir / once_file]
        if not files[0].exists():
            print(f"ERROR: File not found: {files[0]}", file=sys.stderr)
            return 1
    else:
        files = sorted(approved_dir.glob("APPROVAL_*.md"), key=lambda p: p.stat().st_mtime)

    if not files:
        return 0

    print(f"Found {len(files)} approval(s) to process{' [DRY RUN]' if dry_run else ''}.\n")

    results = []
    for f in files:
        r = execute_approval(f, vault, dry_run=dry_run)
        results.append(r)

    success = sum(1 for r in results if r["status"] in ("success", "dry_run"))
    errors  = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skipped")

    print(f"\n── Summary ──────────────────────────────────")
    print(f"  Processed: {success}  |  Errors: {errors}  |  Skipped: {skipped}")
    return 0 if errors == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Approval Executor — dispatch all approved vault actions."
    )
    parser.add_argument("--vault", default="./vault",
                        help="Path to Obsidian vault (default: ./vault)")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Preview without executing any actions")
    parser.add_argument("--once-file", metavar="FILENAME",
                        help="Execute only this specific approval file")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--loop", action="store_true",
                      help="Run continuously, polling every --interval seconds (PM2 mode)")
    parser.add_argument("--interval", type=int, default=30,
                        metavar="SECS",
                        help="Poll interval in seconds for --loop mode (default: 30)")
    args = parser.parse_args()

    vault        = Path(args.vault).expanduser().resolve()
    approved_dir = vault / "Approved"
    approved_dir.mkdir(parents=True, exist_ok=True)

    if args.loop:
        import logging
        logging.basicConfig(
            format="%(asctime)s - ApprovalExecutor - %(levelname)s - %(message)s",
            level=logging.INFO,
        )
        log = logging.getLogger("ApprovalExecutor")
        log.info(f"Loop mode started (interval={args.interval}s). Ctrl+C to stop.")
        while True:
            try:
                files = list(approved_dir.glob("APPROVAL_*.md"))
                if files:
                    log.info(f"{len(files)} approval(s) pending — processing...")
                    _run_once(vault, approved_dir, args.dry_run, None)
                else:
                    log.debug("No approvals pending.")
            except KeyboardInterrupt:
                log.info("Shutdown requested — exiting cleanly.")
                break
            except Exception as exc:
                log.error(f"Unhandled error: {exc}", exc_info=True)
            time.sleep(args.interval)
    else:
        if not list(approved_dir.glob("APPROVAL_*.md")) and not args.once_file:
            print("No pending approvals found in vault/Approved/")
            sys.exit(0)
        sys.exit(_run_once(vault, approved_dir, args.dry_run, args.once_file))


if __name__ == "__main__":
    main()
