"""
Planning & Orchestration Engine — Phase 2, Silver Tier.

Responsibilities
----------------
  1. Scan  vault/Needs_Action/  for new *.md items
  2. Parse YAML frontmatter from each item
  3. Classify: source, priority, risk, requires_approval
  4. Generate  vault/Plans/PLAN_*.md
  5. Generate  vault/Pending_Approval/APPROVAL_*.md  (when approval needed)
  6. Move original item  →  vault/Done/
  7. Update  vault/Dashboard.md  with live counts + recent activity
  8. Write JSONL audit entries to  vault/Logs/YYYY-MM-DD.jsonl
  9. Maintain dedup state in  vault/Logs/planning_state.json

Security rules (hard-coded, non-negotiable)
--------------------------------------------
  - NEVER send emails, WhatsApp messages, or LinkedIn posts/DMs
  - NEVER make payments or trigger financial actions
  - NEVER modify files outside the vault directory
  - NEVER delete originals — only move them to /Done
  - ALWAYS create an approval request before any external action

Usage
-----
    # Process all pending items once, then exit:
    python -m orchestrator.planning_engine --vault ./vault --once

    # Continuous loop (poll every 30 s):
    python -m orchestrator.planning_engine --vault ./vault --loop --interval 30
"""

import json
import logging
import logging.handlers
import argparse
import re
import sys
import time
import textwrap
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pyyaml\nRun: pip install pyyaml")

# ---------------------------------------------------------------------------
# Logging — console + rotating file (written alongside watcher logs)
# ---------------------------------------------------------------------------

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _setup_logging() -> None:
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    fmtr = logging.Formatter(fmt)

    ch = logging.StreamHandler()
    ch.setFormatter(fmtr)

    fh = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "planning_engine.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmtr)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(ch)
        root.addHandler(fh)


_setup_logging()

# ---------------------------------------------------------------------------
# Risk & approval classification tables
# ---------------------------------------------------------------------------

_HIGH_RISK_KEYWORDS = frozenset({
    "money", "legal", "threat", "complaint", "fraud", "scam", "lawsuit",
    "court", "police", "blackmail", "hack", "breach", "stolen", "dispute",
    "emergency", "critical", "overdue", "terminate", "suspend", "banned",
    "illegal", "attorney", "solicitor", "chargeback", "arbitration",
})

_MEDIUM_RISK_KEYWORDS = frozenset({
    "pricing", "price", "proposal", "hire", "hiring", "negotiate",
    "negotiation", "partnership", "contract", "agreement", "deal", "offer",
    "quote", "quotation", "budget", "revenue", "sales", "client", "customer",
    "invoice", "payment", "refund", "purchase", "subscription", "retainer",
})

_APPROVAL_TRIGGERS = frozenset({
    "urgent", "payment", "invoice", "refund", "pricing", "quote", "budget",
    "contract", "complaint", "asap", "money", "transfer", "bank", "pay",
    "send", "post", "publish", "reply", "respond",
})

# Sources that always require approval (any external communication)
_EXTERNAL_SOURCES = {"gmail", "whatsapp", "linkedin"}


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def classify_risk(text: str) -> str:
    w = _word_set(text)
    if w & _HIGH_RISK_KEYWORDS:
        return "high"
    if w & _MEDIUM_RISK_KEYWORDS:
        return "medium"
    return "low"


def needs_approval(text: str, source: str, risk: str) -> bool:
    if source in _EXTERNAL_SOURCES:
        return True
    if risk == "high":
        return True
    if _word_set(text) & _APPROVAL_TRIGGERS:
        return True
    return False


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def parse_md(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_text). Gracefully handles missing FM."""
    m = _FM_RE.match(text)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        return fm, m.group(2).strip()
    return {}, text.strip()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_id(text: str, max_len: int = 36) -> str:
    """Convert arbitrary text to a filesystem-safe slug."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(text).strip())[:max_len].strip("_")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    return date.today().isoformat()


def _sender(fm: dict) -> str:
    """Extract the most human-readable sender/name from frontmatter."""
    raw = (
        fm.get("from")
        or fm.get("sender")
        or fm.get("name")
        or "Unknown"
    )
    # Strip email address angle-bracket part: "John Doe <john@example.com>" → "John Doe"
    return raw.split("<")[0].strip()


def _subject(fm: dict) -> str:
    return (
        fm.get("subject")
        or fm.get("topic")
        or fm.get("kind", "").replace("_", " ").title()
        or "N/A"
    )


# ---------------------------------------------------------------------------
# Source → approval action mapping
# ---------------------------------------------------------------------------

_ACTION_MAP = {
    "gmail":    "send_email",
    "whatsapp": "send_whatsapp",
    "linkedin": "send_linkedin_dm",
}


def action_for(source: str, fm: dict) -> str:
    if source == "linkedin" and fm.get("kind") == "connection_request":
        return "send_linkedin_connection_reply"
    return _ACTION_MAP.get(source, "send_message")


# ---------------------------------------------------------------------------
# Draft generators — template-based, source-specific
# ---------------------------------------------------------------------------

def _draft_email(fm: dict, body: str) -> str:
    name    = _sender(fm)
    subject = _subject(fm)
    snippet = re.sub(r"#+.*?\n", "", body)[:300].strip().replace("\n", " ")
    return textwrap.dedent(f"""\
        Subject: Re: {subject}

        Dear {name},

        Thank you for your email regarding "{subject}".

        I have reviewed your message:
        > {snippet}

        I will provide a detailed response shortly.

        [TODO — personalise this draft before approving the send action.]

        Best regards,
        [Your Name]
    """)


def _draft_whatsapp(fm: dict, body: str) -> str:
    name = _sender(fm)
    preview_m = re.search(r"### Message Preview\s*\n+(.*?)(?:\n##|$)", body, re.DOTALL)
    preview   = preview_m.group(1).strip()[:120] if preview_m else body[:120]
    return textwrap.dedent(f"""\
        Hi {name}! 👋

        Thanks for your message:
        "{preview}"

        I'll get back to you with a full response very soon.

        [TODO — personalise before approving the send action.]
    """)


def _draft_linkedin(fm: dict, body: str) -> str:
    name = _sender(fm)
    kind = fm.get("kind", "dm")
    msg_m = re.search(r"### Message[^#\n]*\n+(.*?)(?:\n##|$)", body, re.DOTALL)
    msg   = msg_m.group(1).strip()[:200] if msg_m else ""

    if kind == "connection_request":
        return textwrap.dedent(f"""\
            Hi {name},

            Thank you for connecting! I'm always happy to expand my professional network.

            I'd love to learn more about what you do and explore potential collaboration.

            Looking forward to connecting!

            [TODO — personalise before approving.]
        """)

    return textwrap.dedent(f"""\
        Hi {name},

        Thank you for your message{f': "{msg[:80]}"' if msg else ''}.

        I appreciate you reaching out. I'd be happy to discuss this further —
        could you share a bit more detail so I can give you the best response?

        [TODO — personalise before approving the send action.]

        Best,
        [Your Name]
    """)


def _draft_file(fm: dict, body: str) -> str:
    filename = fm.get("original_name", "the file")
    size     = fm.get("size_bytes", "unknown")
    return textwrap.dedent(f"""\
        File received: {filename}  ({size} bytes)

        Review checklist:
        1. Open and inspect the file contents
        2. Identify required action (respond / archive / process)
        3. Draft appropriate response if needed

        [TODO — complete this section after reviewing the file.]
    """)


_DRAFT_GENERATORS = {
    "gmail":     _draft_email,
    "whatsapp":  _draft_whatsapp,
    "linkedin":  _draft_linkedin,
    "file_drop": _draft_file,
}


def generate_draft(source: str, fm: dict, body: str) -> str:
    gen = _DRAFT_GENERATORS.get(source, _draft_file)
    return gen(fm, body)


# ---------------------------------------------------------------------------
# Plan.md builder
# ---------------------------------------------------------------------------

def build_plan(
    *,
    source_file: Path,
    vault_path: Path,
    source: str,
    fm: dict,
    body: str,
    priority: str,
    risk: str,
    approval_needed: bool,
    plan_path: Path,
    approval_path: Path | None,
) -> str:
    rel_source  = source_file.relative_to(vault_path)
    sender_disp = _sender(fm)
    subj_disp   = _subject(fm)
    summary     = textwrap.fill(
        re.sub(r"#+.*?\n|---.*?---", "", body, flags=re.DOTALL)[:400]
        .replace("\n", " ").strip(),
        width=80,
    )
    draft       = generate_draft(source, fm, body)
    draft_block = textwrap.indent(draft.strip(), "    ")

    if approval_path:
        approval_gate = (
            f"**Approval required.** Approval request created at:\n"
            f"`{approval_path.relative_to(vault_path)}`\n\n"
            f"Move that file to `/Approved` to authorise the action, "
            f"or `/Rejected` to discard it."
        )
    else:
        approval_gate = (
            "No external action planned for this item. "
            "No approval file needed."
        )

    approval_task = (
        "- [ ] Approve or reject via `/Pending_Approval/`"
        if approval_needed
        else "- [ ] _(no approval needed — internal item)_"
    )

    return f"""\
---
type: plan
source_file: {rel_source}
source: {source}
created: {_now_iso()}
status: planned
priority: {priority}
risk: {risk}
requires_approval: {str(approval_needed).lower()}
---

# Objective
Process and respond to **{source}** item from **{sender_disp}** regarding "{subj_disp}".

# Context
- **Origin:** {source}
- **Sender / Name:** {sender_disp}
- **Subject / Topic:** {subj_disp}
- **Received:** {fm.get('received', 'unknown')}
- **Priority:** {priority.upper()}
- **Risk:** {risk.upper()}

## Summary
{summary}

# Assumptions
- This is the **draft-only phase** — no external actions are executed automatically.
- All outbound responses require explicit human approval via `/Pending_Approval/`.
- Rules of engagement defined in `Company_Handbook.md` apply.

# Plan
- [ ] Review the context and summary above
- [ ] Read and refine the draft output below
- [ ] Verify tone, accuracy, and completeness
{approval_task}
- [ ] Once approved (or no action needed), log result and archive original

# Draft Output _(DRAFT ONLY — DO NOT SEND WITHOUT APPROVAL)_

{draft_block}

# Approval Gate
{approval_gate}

# Completion Criteria
- [ ] Original item moved to `/Done`
- [ ] Audit log entry written to `/Logs/`
- [ ] `Dashboard.md` updated with latest counts
- [ ] {"Approval request present in `/Pending_Approval/`" if approval_needed else "No approval required — task may be closed directly"}
"""


# ---------------------------------------------------------------------------
# Approval.md builder
# ---------------------------------------------------------------------------

def build_approval(
    *,
    action: str,
    plan_path: Path,
    vault_path: Path,
    source: str,
    fm: dict,
    body: str,
    draft: str,
) -> str:
    rel_plan      = plan_path.relative_to(vault_path)
    target        = (
        fm.get("from")
        or fm.get("sender")
        or fm.get("name")
        or fm.get("profile")
        or "Unknown"
    )
    subject_title = _subject(fm)
    action_label  = action.replace("_", " ").title()
    draft_block   = textwrap.indent(draft.strip(), "  ")

    return f"""\
---
type: approval_request
action: {action}
source_plan: {rel_plan}
created: {_now_iso()}
status: pending
---

# What will happen after approval?

The following **{action_label}** will be executed via the appropriate MCP server.

> ⚠️  **No action is taken until this file is moved to `/Approved`.**
> Moving it to `/Rejected` will discard the request without any action.

# Payload

- **Action:** `{action}`
- **Target:** {target}
- **Subject / Title:** {subject_title}

## Message / Content

{draft_block}

# How to Approve or Reject

| Decision | Action |
|----------|--------|
| ✅ Approve | Move this file to `/Approved` |
| ❌ Reject  | Move this file to `/Rejected` |

---
*Generated by Planning Engine — review carefully before approving.*
"""


# ---------------------------------------------------------------------------
# State store — prevents reprocessing across restarts
# ---------------------------------------------------------------------------

class StateStore:
    def __init__(self, vault_path: Path):
        self._path = vault_path / "Logs" / "planning_state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"processed": {}}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, indent=2, default=str),
            encoding="utf-8",
        )

    def is_processed(self, filename: str) -> bool:
        return filename in self._data["processed"]

    def mark_processed(
        self,
        filename: str,
        *,
        plan_path: str,
        approval_path: str | None,
        timestamp: str,
    ) -> None:
        self._data["processed"][filename] = {
            "timestamp":     timestamp,
            "plan":          plan_path,
            "approval":      approval_path,
        }
        self._save()


# ---------------------------------------------------------------------------
# JSONL audit logger
# ---------------------------------------------------------------------------

class AuditLog:
    def __init__(self, vault_path: Path):
        self._log_dir = vault_path / "Logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def _path(self) -> Path:
        return self._log_dir / f"{_today_str()}.jsonl"

    def write(self, event: str, **kwargs: Any) -> None:
        entry = {"timestamp": _now_iso(), "event": event, **kwargs}
        with self._path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Dashboard updater
# ---------------------------------------------------------------------------

def update_dashboard(vault_path: Path, recent_activities: list[str]) -> None:
    def _count(folder: str) -> int:
        return sum(
            1 for p in (vault_path / folder).glob("*.md")
            if p.name != ".gitkeep"
        )

    na  = _count("Needs_Action")
    pl  = _count("Plans")
    pa  = _count("Pending_Approval")
    ap  = _count("Approved")
    rj  = _count("Rejected")
    dn  = _count("Done")

    activity_block = "\n".join(
        f"- {a}" for a in recent_activities[-10:]
    ) or "_No recent activity this session._"

    content = f"""\
---
updated: {_now_iso()}
---

# AI Employee — Dashboard

## Live Vault Counts

| Folder             | Files |
|--------------------|-------|
| 📥 Needs Action    | {na}     |
| 📋 Plans           | {pl}     |
| ⏳ Pending Approval | {pa}     |
| ✅ Approved        | {ap}     |
| ❌ Rejected        | {rj}     |
| 🗂 Done            | {dn}     |

## Recent Activity

{activity_block}

---
*Updated by Planning Engine — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*
"""
    (vault_path / "Dashboard.md").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class PlanningEngine:
    """Scans Needs_Action, generates Plans and Approval requests, archives originals."""

    def __init__(self, vault_path: Path):
        self.vault   = vault_path.resolve()
        self.log     = logging.getLogger("PlanningEngine")
        self.state   = StateStore(self.vault)
        self.audit   = AuditLog(self.vault)
        self._recent: list[str] = []

        # Ensure all required vault directories exist
        for folder in (
            "Needs_Action", "Plans", "Pending_Approval",
            "Approved", "Rejected", "Done", "Logs",
        ):
            (self.vault / folder).mkdir(parents=True, exist_ok=True)

        self.log.info(f"PlanningEngine ready — vault: {self.vault}")

    # ------------------------------------------------------------------
    # Priority heuristic
    # ------------------------------------------------------------------

    def _priority(self, fm: dict, risk: str) -> str:
        fm_priority = str(fm.get("priority", "")).lower()
        if fm_priority == "high" or risk == "high":
            return "high"
        if fm_priority == "medium" or risk == "medium":
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Plan filename — unique per source-file to prevent collisions
    # ------------------------------------------------------------------

    def _plan_filename(
        self, source: str, sender_raw: str, file_stem: str
    ) -> str:
        src_tag    = _safe_id(source.upper(), 12)
        sender_tag = _safe_id(sender_raw, 24)
        file_tag   = _safe_id(file_stem, 20)
        return f"PLAN_{src_tag}_{sender_tag}_{file_tag}_{_today_str()}.md"

    def _approval_filename(
        self, action: str, sender_raw: str, file_stem: str
    ) -> str:
        act_tag    = _safe_id(action.upper(), 28)
        sender_tag = _safe_id(sender_raw, 20)
        file_tag   = _safe_id(file_stem, 16)
        return f"APPROVAL_{act_tag}_{sender_tag}_{file_tag}_{_today_str()}.md"

    # ------------------------------------------------------------------
    # Single-file processor
    # ------------------------------------------------------------------

    def _process_file(self, md_file: Path) -> None:
        filename = md_file.name

        if self.state.is_processed(filename):
            self.log.debug(f"Already processed: {filename}")
            return

        self.log.info(f"Processing: {filename}")
        ts = _now_iso()

        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            self.log.error(f"Cannot read {filename}: {exc}")
            return

        fm, body     = parse_md(text)
        full_text    = " ".join(str(v) for v in fm.values()) + " " + body

        source       = str(fm.get("source", "file_drop")).lower()
        risk         = classify_risk(full_text)
        approval     = needs_approval(full_text, source, risk)
        priority     = self._priority(fm, risk)
        sender_raw   = _sender(fm)

        # ── Plan path ─────────────────────────────────────────────────
        plan_name    = self._plan_filename(source, sender_raw, md_file.stem)
        plan_path    = self.vault / "Plans" / plan_name

        # ── Approval path ──────────────────────────────────────────────
        approval_path: Path | None = None
        if approval:
            action        = action_for(source, fm)
            appr_name     = self._approval_filename(action, sender_raw, md_file.stem)
            approval_path = self.vault / "Pending_Approval" / appr_name

        # ── Write Plan ─────────────────────────────────────────────────
        plan_content = build_plan(
            source_file     = md_file,
            vault_path      = self.vault,
            source          = source,
            fm              = fm,
            body            = body,
            priority        = priority,
            risk            = risk,
            approval_needed = approval,
            plan_path       = plan_path,
            approval_path   = approval_path,
        )
        plan_path.write_text(plan_content, encoding="utf-8")
        self.log.info(f"  Plan      → Plans/{plan_name}")

        # ── Write Approval ─────────────────────────────────────────────
        if approval_path:
            draft        = generate_draft(source, fm, body)
            action_str   = action_for(source, fm)
            appr_content = build_approval(
                action      = action_str,
                plan_path   = plan_path,
                vault_path  = self.vault,
                source      = source,
                fm          = fm,
                body        = body,
                draft       = draft,
            )
            approval_path.write_text(appr_content, encoding="utf-8")
            self.log.info(f"  Approval  → Pending_Approval/{approval_path.name}")

        # ── Move original to Done ──────────────────────────────────────
        done_dest = self.vault / "Done" / filename
        if done_dest.exists():  # avoid collision
            stem, sfx = done_dest.stem, done_dest.suffix
            done_dest = done_dest.with_name(f"{stem}_{int(time.time())}{sfx}")
        md_file.rename(done_dest)
        self.log.info(f"  Archived  → Done/{done_dest.name}")

        # ── Persist state ──────────────────────────────────────────────
        self.state.mark_processed(
            filename,
            plan_path     = str(plan_path.relative_to(self.vault)),
            approval_path = (
                str(approval_path.relative_to(self.vault))
                if approval_path else None
            ),
            timestamp = ts,
        )

        # ── Audit log ──────────────────────────────────────────────────
        self.audit.write(
            "item_processed",
            source    = source,
            filename  = filename,
            plan      = plan_name,
            approval  = approval_path.name if approval_path else None,
            priority  = priority,
            risk      = risk,
            result    = "success",
        )

        # ── Dashboard activity line ────────────────────────────────────
        ts_short = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        appr_note = f" + `{approval_path.name}`" if approval_path else ""
        self._recent.append(
            f"`{ts_short}` [{source.upper()}] {sender_raw[:35]} "
            f"→ `{plan_name}`{appr_note}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_once(self) -> int:
        """Process all pending items in Needs_Action. Returns count processed."""
        pending = sorted(
            (
                p for p in (self.vault / "Needs_Action").glob("*.md")
                if p.name != ".gitkeep"
            ),
            key=lambda p: p.stat().st_mtime,
        )

        if not pending:
            self.log.info("Needs_Action is empty — nothing to process.")
            update_dashboard(self.vault, self._recent)
            return 0

        self.log.info(f"Found {len(pending)} item(s) in Needs_Action.")
        processed = 0

        for md_file in pending:
            try:
                self._process_file(md_file)
                processed += 1
            except Exception as exc:
                self.log.error(
                    f"Failed to process {md_file.name}: {exc}", exc_info=True
                )
                self.audit.write(
                    "item_error",
                    filename = md_file.name,
                    error    = str(exc),
                    result   = "error",
                )

        update_dashboard(self.vault, self._recent)
        self.log.info(f"Cycle complete — {processed}/{len(pending)} item(s) processed.")
        return processed

    def run_loop(self, interval: int = 30) -> None:
        """Run continuously, polling every `interval` seconds."""
        self.log.info(f"Loop mode started (interval={interval}s). Ctrl+C to stop.")
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                self.log.info("Shutdown requested — exiting cleanly.")
                break
            except Exception as exc:
                self.log.error(f"Unhandled error in run loop: {exc}", exc_info=True)
            time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Employee — Planning & Orchestration Engine (Phase 2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python -m orchestrator.planning_engine --vault ./vault --once
              python -m orchestrator.planning_engine --vault ./vault --loop --interval 60
        """),
    )
    parser.add_argument(
        "--vault",
        default="./vault",
        metavar="PATH",
        help="Path to the Obsidian vault directory (default: ./vault)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--once",
        action="store_true",
        help="Process all pending items and exit.",
    )
    mode.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously, polling every --interval seconds.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        metavar="SECS",
        help="Poll interval in seconds for --loop mode (default: 30).",
    )
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.exists():
        print(f"ERROR: Vault path not found: {vault}", file=sys.stderr)
        sys.exit(1)

    engine = PlanningEngine(vault)

    if args.once:
        count = engine.run_once()
        sys.exit(0 if count >= 0 else 1)
    else:
        engine.run_loop(interval=args.interval)


if __name__ == "__main__":
    main()
