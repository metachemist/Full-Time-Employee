#!/usr/bin/env python3
"""
Approval Executor — AI Employee Gold Tier skill script.

Scans vault/Approved/ for pending APPROVAL_*.md files, parses each one,
dispatches to the appropriate sender script, then moves to vault/Done/
and writes the audit log.

Usage:
    python execute.py --vault ./vault               # execute all approvals
    python execute.py --vault ./vault --dry-run     # preview without executing
    python execute.py --vault ./vault --once-file APPROVAL_SEND_EMAIL_...md
    python execute.py --vault ./vault --loop        # daemon mode (PM2 / cron alternative)
    python execute.py --vault ./vault --loop --interval 60
    python execute.py --vault ./vault --retry-failed  # move Failed/ back to Approved/ for retry
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Playwright-based actions (need longer subprocess timeout)
# ---------------------------------------------------------------------------

_PLAYWRIGHT_ACTIONS = {"send_twitter_post"}

# ---------------------------------------------------------------------------
# Subprocess resource limits — applied via preexec_fn on POSIX systems.
# Prevents runaway skill scripts from consuming unbounded CPU/memory.
# ---------------------------------------------------------------------------

try:
    import resource as _resource
    _HAVE_RESOURCE = True
except ImportError:
    _HAVE_RESOURCE = False  # Windows — skip silently


def _set_subprocess_limits() -> None:
    """Called by subprocess preexec_fn. Sets per-process resource limits."""
    if not _HAVE_RESOURCE:
        return
    # CPU time: 7 minutes hard cap (Playwright actions can be slow)
    _resource.setrlimit(_resource.RLIMIT_CPU, (420, 420))
    # Max open file descriptors: 256 (prevent fd leak escalation)
    try:
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (256, 256))
    except ValueError:
        pass  # current limit already lower than 256 — leave it


# ---------------------------------------------------------------------------
# Email recipient allowlist + RFC5322 validation
# ---------------------------------------------------------------------------

_log = logging.getLogger("ApprovalExecutor")

_EMAIL_ALLOWLIST_DOMAINS: set[str] = set(
    d.strip().lower()
    for d in os.environ.get("EMAIL_RECIPIENT_ALLOWLIST", "").split(",")
    if d.strip()
)

# Simple but sufficient RFC5322 local@domain validator
_EMAIL_RE = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)


def _validate_email_recipient(to: str) -> str | None:
    """Validate an email recipient.

    Checks (in order):
    1. No null bytes or ASCII control characters
    2. Single address only (no commas / semicolons — prevents CC injection)
    3. Valid RFC5322-ish format
    4. Domain in EMAIL_RECIPIENT_ALLOWLIST (if configured)

    Returns an error string on the first failure, or None if valid.
    """
    if not to or not to.strip():
        return "Recipient 'to' field is empty."

    # Strip display-name portion: "John <john@example.com>" → "john@example.com"
    m = re.search(r'<([^>]+)>', to)
    bare = m.group(1).strip() if m else to.strip()

    # Null bytes / control characters
    if any(ord(c) < 32 for c in bare):
        return "Recipient contains invalid control characters."

    # Multiple addresses (comma or semicolon separated)
    if re.search(r'[,;]', bare):
        return "Multiple recipients are not allowed — provide a single address."

    # Basic format check
    if not _EMAIL_RE.match(bare):
        return f"Recipient '{bare}' is not a valid email address."

    # Domain allowlist (optional — skipped if not configured)
    if _EMAIL_ALLOWLIST_DOMAINS:
        domain = bare.split("@")[-1].lower()
        if domain not in _EMAIL_ALLOWLIST_DOMAINS:
            return (
                f"Recipient domain '{domain}' not in EMAIL_RECIPIENT_ALLOWLIST. "
                f"Allowed: {', '.join(sorted(_EMAIL_ALLOWLIST_DOMAINS))}"
            )
    else:
        _log.warning("EMAIL_RECIPIENT_ALLOWLIST not set — all recipient domains allowed")

    return None


# ---------------------------------------------------------------------------
# Rate limiting — persisted to disk so counter survives PM2 restarts
# ---------------------------------------------------------------------------

_MAX_ACTIONS_PER_HOUR = 10
_rate_state: dict = {"bucket": None, "count": 0}
_rate_file: Path | None = None  # set once vault path is known
_rate_lock = threading.Lock()   # guards _rate_state across concurrent approvals


def _rate_file_path(vault: Path) -> Path:
    return vault / "Logs" / "rate_limit.json"


def _load_rate_state(vault: Path) -> None:
    """Load persisted rate counter from disk on startup."""
    global _rate_state, _rate_file
    _rate_file = _rate_file_path(vault)
    if _rate_file.exists():
        try:
            data = json.loads(_rate_file.read_text(encoding="utf-8"))
            bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
            if data.get("bucket") == bucket:
                _rate_state = data
            # else: different hour — start fresh
        except (json.JSONDecodeError, OSError):
            pass


def _save_rate_state() -> None:
    """Persist current rate counter atomically."""
    if _rate_file is None:
        return
    try:
        tmp = _rate_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(_rate_state), encoding="utf-8")
        os.replace(tmp, _rate_file)
    except OSError:
        pass


def _rate_limit_check() -> bool:
    """Return True if action is allowed; False if hourly cap reached.

    Thread-safe via _rate_lock.
    """
    with _rate_lock:
        bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        if _rate_state["bucket"] != bucket:
            _rate_state["bucket"] = bucket
            _rate_state["count"] = 0
        if _rate_state["count"] >= _MAX_ACTIONS_PER_HOUR:
            return False
        _rate_state["count"] += 1
        _save_rate_state()
        return True


# ---------------------------------------------------------------------------
# Metrics — accumulated counters written to vault/Logs/metrics.json
# ---------------------------------------------------------------------------

_metrics: dict = {
    "actions_total":        0,
    "actions_success":      0,
    "actions_failed":       0,
    "actions_skipped":      0,
    "actions_rate_limited": 0,
    "last_updated":         None,
}
_metrics_file: Path | None = None
_metrics_lock = threading.Lock()  # guards _metrics across concurrent approvals


def _load_metrics(vault: Path) -> None:
    global _metrics, _metrics_file
    _metrics_file = vault / "Logs" / "metrics.json"
    if _metrics_file.exists():
        try:
            data = json.loads(_metrics_file.read_text(encoding="utf-8"))
            for k in _metrics:
                if k in data:
                    _metrics[k] = data[k]
        except (json.JSONDecodeError, OSError):
            pass


def _write_metrics(vault: Path) -> None:
    if _metrics_file is None:
        return
    _metrics["last_updated"] = _ts()
    try:
        tmp = _metrics_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(_metrics, indent=2), encoding="utf-8")
        os.replace(tmp, _metrics_file)
    except OSError:
        pass


def _record_metric(key: str, vault: Path) -> None:
    with _metrics_lock:
        _metrics["actions_total"] += 1
        if key in _metrics:
            _metrics[key] += 1
    _write_metrics(vault)


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
    "send_email":                    _SKILLS_DIR / "gmail-sender"      / "scripts" / "send_email.py",
    "send_linkedin_post":            _SKILLS_DIR / "linkedin-poster"   / "scripts" / "create_post.py",
    "send_twitter_post":             _SKILLS_DIR / "twitter-poster"    / "scripts" / "create_post.py",
    "send_facebook_post":            _SKILLS_DIR / "facebook-poster"   / "scripts" / "create_post.py",
    "send_instagram_post":           _SKILLS_DIR / "instagram-poster"  / "scripts" / "create_post.py",
    "odoo_create_lead":              _SKILLS_DIR / "odoo-crm"          / "scripts" / "odoo_client.py",
    "odoo_create_draft_invoice":     _SKILLS_DIR / "odoo-crm"          / "scripts" / "odoo_client.py",
    "odoo_log_activity":             _SKILLS_DIR / "odoo-crm"          / "scripts" / "odoo_client.py",
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
        err = _validate_email_recipient(target)
        if err:
            raise ValueError(err)
        args = ["--to", target, "--subject", subject or "(no subject)", "--body", message]
        return args

    if action == "send_linkedin_post":
        if not message:
            return None
        return ["--content", message]

    if action == "send_twitter_post":
        if not message:
            return None
        return ["--content", message]

    if action == "send_facebook_post":
        if not message:
            return None
        return ["--content", message]

    if action == "send_instagram_post":
        # target field holds the public HTTPS image URL; message is the caption
        if not message or not target:
            return None
        return ["--caption", message, "--image-url", target]

    if action == "odoo_create_lead":
        # Payload: Target = partner name, Message = description
        # Optional Subject = lead title (falls back to partner name)
        name = subject or target or "New Lead from AI Employee"
        data = {"name": name}
        if target:
            data["partner_name"] = target
        if message:
            data["description"] = message
        email_field = _extract_field(body, "Email")
        if email_field:
            data["email"] = email_field
        return ["--operation", "create_lead", "--data", json.dumps(data)]

    if action == "odoo_create_draft_invoice":
        # Payload: Target = partner name, Message = JSON lines array or description
        partner = target or ""
        lines = []
        try:
            lines = json.loads(message) if message else []
        except (json.JSONDecodeError, TypeError):
            if message:
                lines = [{"name": message, "price_unit": 0, "quantity": 1}]
        data = {"partner_name": partner, "lines": lines}
        return ["--operation", "create_draft_invoice", "--data", json.dumps(data)]

    if action == "odoo_log_activity":
        # Payload: Target = "model:record_id", Subject = summary, Message = note
        model, _, record_id_str = (target or "crm.lead:0").partition(":")
        try:
            record_id = int(record_id_str)
        except ValueError:
            record_id = 0
        data = {
            "model":     model,
            "record_id": record_id,
            "summary":   subject or "AI Employee activity",
            "note":      message or "",
        }
        return ["--operation", "log_activity", "--data", json.dumps(data)]

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

def _alert_failure(vault: Path, filename: str, action: str, error: str) -> None:
    """Write a CRITICAL alert to vault/Needs_Action/ when an action fails."""
    try:
        now = datetime.now(timezone.utc)
        alert_name = f"CRITICAL_FAILED_{action.upper()}_{now.strftime('%Y%m%d_%H%M%S')}.md"
        content = f"""\
---
type: alert
priority: critical
source: approval-executor
created: {now.isoformat()}
status: pending
---

# Action Failed: {action}

An approved action failed to execute and requires attention.

- **Failed File:** `{filename}`
- **Action:** `{action}`
- **Error:** {error}
- **Time:** {now.strftime('%Y-%m-%d %H:%M UTC')}

## Required Action

1. Check `vault/Failed/{filename}` for full error details
2. Fix the underlying issue (expired token, invalid recipient, etc.)
3. Move the file back to `vault/Approved/` to retry, or `vault/Rejected/` to discard

*Generated by Approval Executor — review immediately.*
"""
        alert_path = vault / "Needs_Action" / alert_name
        alert_path.parent.mkdir(parents=True, exist_ok=True)
        alert_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        _log.error(f"Could not write failure alert: {exc}")


def execute_approval(
    approval_file: Path,
    vault: Path,
    dry_run: bool = False,
    failed_dir: Path | None = None,
) -> dict:
    text = approval_file.read_text(encoding="utf-8")
    fm, body = _parse_approval(text)

    action = str(fm.get("action", "")).lower()
    status = str(fm.get("status", "")).lower()
    trace_id = str(fm.get("trace_id", ""))

    if status in ("sent", "posted", "failed"):
        _record_metric("actions_skipped", vault)
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

    try:
        extra_args = _build_args(action, body)
    except ValueError as exc:
        return {"status": "error", "reason": str(exc), "file": approval_file.name}
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
        _audit(vault, "rate_limited", action=action, file=approval_file.name,
               limit=_MAX_ACTIONS_PER_HOUR, **( {"trace_id": trace_id} if trace_id else {} ))
        _record_metric("actions_rate_limited", vault)
        return {"status": "skipped", "reason": msg, "file": approval_file.name}

    # ── Execute ──────────────────────────────────────────────────────────
    _failed_dir = failed_dir or (vault / "Failed")
    _failed_dir.mkdir(parents=True, exist_ok=True)

    # Playwright actions need more time for browser startup + page rendering
    _timeout = 300 if action in _PLAYWRIGHT_ACTIONS else 120

    # preexec_fn applies resource limits to the skill subprocess (POSIX only)
    _preexec = _set_subprocess_limits if _HAVE_RESOURCE else None

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_timeout, preexec_fn=_preexec,
        )
        output_raw = result.stdout.strip()
        try:
            output = json.loads(output_raw)
        except json.JSONDecodeError:
            output = {"raw": output_raw}

        success = result.returncode == 0 and output.get("status") not in ("error", "failed")
        new_status = output.get("status", "sent" if success else "failed")
        stderr_snippet = result.stderr.strip()[:400] if result.stderr else ""

        # ── Update approval file ──────────────────────────────────────────
        updated_text = re.sub(r"^status:\s*.+$", f"status: {new_status}", text, flags=re.MULTILINE)
        updated_text += f"\n<!-- executed_at: {_ts()} -->\n"
        if not success and stderr_snippet:
            updated_text += f"<!-- error: {stderr_snippet[:200]} -->\n"
        approval_file.write_text(updated_text, encoding="utf-8")

        # ── Route: success → Done/, failure → Failed/ ─────────────────────
        dest_dir = vault / "Done" if success else _failed_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / approval_file.name
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{int(datetime.now().timestamp())}.md")
        approval_file.rename(dest)

        # ── Alert on failure ──────────────────────────────────────────────
        if not success:
            _alert_failure(vault, approval_file.name, action, stderr_snippet or new_status)

        # ── Audit ─────────────────────────────────────────────────────────
        _audit(vault, "action_executed", action=action, file=approval_file.name,
               result="success" if success else "error",
               output=output, returncode=result.returncode,
               stderr=stderr_snippet or None,
               destination="Done" if success else "Failed",
               **( {"trace_id": trace_id} if trace_id else {} ))

        _record_metric("actions_success" if success else "actions_failed", vault)

        status_icon = "✓" if success else "✗"
        print(f"  {status_icon} {new_status.upper()}", end="")
        if not success:
            print(f" → vault/Failed/{dest.name}")
            if stderr_snippet:
                print(f"  stderr: {stderr_snippet[:200]}")
        else:
            print()

        return {
            "status": "success" if success else "error",
            "action": action,
            "file":   approval_file.name,
            "result": output,
            "destination": "Done" if success else "Failed",
        }

    except subprocess.TimeoutExpired:
        updated_text = re.sub(r"^status:\s*.+$", "status: timeout", text, flags=re.MULTILINE)
        updated_text += f"\n<!-- executed_at: {_ts()} -->\n<!-- error: timed out after {_timeout}s -->\n"
        approval_file.write_text(updated_text, encoding="utf-8")
        dest = _failed_dir / approval_file.name
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{int(datetime.now().timestamp())}.md")
        approval_file.rename(dest)
        _alert_failure(vault, approval_file.name, action, f"Timed out after {_timeout}s")
        _audit(vault, "action_timeout", action=action, file=approval_file.name,
               destination="Failed", timeout_secs=_timeout,
               **( {"trace_id": trace_id} if trace_id else {} ))
        _record_metric("actions_failed", vault)
        print(f"  ✗ TIMEOUT → vault/Failed/{dest.name}")
        return {"status": "error", "reason": f"Script timed out after {_timeout}s.", "file": approval_file.name}

    except Exception as exc:
        _audit(vault, "action_error", action=action, file=approval_file.name, error=str(exc),
               **( {"trace_id": trace_id} if trace_id else {} ))
        _record_metric("actions_failed", vault)
        return {"status": "error", "reason": str(exc), "file": approval_file.name}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _retry_failed(vault: Path, failed_dir: Path, approved_dir: Path) -> int:
    """Move all files from vault/Failed/ back to vault/Approved/ for retry."""
    files = list(failed_dir.glob("APPROVAL_*.md"))
    if not files:
        print("No failed approvals to retry.")
        return 0

    print(f"Moving {len(files)} failed approval(s) back to Approved/ for retry…")
    for f in files:
        # Reset status to 'approved' in frontmatter before re-queuing
        text = f.read_text(encoding="utf-8")
        text = re.sub(r"^status:\s*.+$", "status: approved", text, flags=re.MULTILINE)
        # Strip old executed_at / error comments
        text = re.sub(r"\n<!-- (executed_at|error):.*?-->\n", "\n", text)
        dest = approved_dir / f.name
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{int(datetime.now().timestamp())}.md")
        f.write_text(text, encoding="utf-8")
        f.rename(dest)
        print(f"  ↩ {f.name} → Approved/")
    return 0


def _run_once(vault: Path, approved_dir: Path, dry_run: bool, once_file: str | None) -> int:
    """Process one batch of approvals concurrently.

    Non-Playwright actions (email, API calls) run in parallel via
    ThreadPoolExecutor. Each Playwright action launches its own subprocess
    with a fresh browser context, so multiple Playwright actions also run
    concurrently (up to MAX_WORKERS_PLAYWRIGHT).

    Returns exit code: 0=ok, 1=errors.
    """
    failed_dir = vault / "Failed"

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

    # Playwright actions get their own limited pool (browser startup is heavy)
    MAX_WORKERS = 4

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(execute_approval, f, vault, dry_run, failed_dir): f
            for f in files
        }
        for future in as_completed(futures):
            try:
                r = future.result()
            except Exception as exc:
                f = futures[future]
                r = {"status": "error", "reason": str(exc), "file": f.name}
            results.append(r)

    success = sum(1 for r in results if r["status"] in ("success", "dry_run"))
    errors  = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skipped")

    print(f"\n── Summary ──────────────────────────────────")
    print(f"  Processed: {success}  |  Errors: {errors}  |  Skipped: {skipped}")
    if errors:
        print(f"  Failed files moved to: vault/Failed/")
        print(f"  To retry: python execute.py --vault {vault} --retry-failed")
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
    mode.add_argument("--retry-failed", action="store_true",
                      help="Move all files from vault/Failed/ back to Approved/ for retry")
    parser.add_argument("--interval", type=int, default=30,
                        metavar="SECS",
                        help="Poll interval in seconds for --loop mode (default: 30)")
    args = parser.parse_args()

    vault        = Path(args.vault).expanduser().resolve()
    approved_dir = vault / "Approved"
    failed_dir   = vault / "Failed"
    approved_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)
    (vault / "Logs").mkdir(parents=True, exist_ok=True)

    # Load persisted state from disk
    _load_rate_state(vault)
    _load_metrics(vault)

    if args.retry_failed:
        sys.exit(_retry_failed(vault, failed_dir, approved_dir))

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
                _audit(vault, "executor_error", error=str(exc))
            time.sleep(args.interval)
    else:
        if not list(approved_dir.glob("APPROVAL_*.md")) and not args.once_file:
            print("No pending approvals found in vault/Approved/")
            sys.exit(0)
        sys.exit(_run_once(vault, approved_dir, args.dry_run, args.once_file))


if __name__ == "__main__":
    main()
