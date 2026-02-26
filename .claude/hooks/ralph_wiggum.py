#!/usr/bin/env python3
"""
Ralph Wiggum Stop Hook — AI Employee Silver Tier.

Intercepts Claude Code's exit and re-injects the task prompt if there is
still pending work in the vault. Named after Ralph Wiggum's persistence
("I'm Idaho!") — Claude keeps going until the job is done.

Exit codes:
  0  → allow Claude to stop (no pending work)
  2  → block stop, inject stdout as a new user message (work remains)

Registered in .claude/settings.local.json under hooks.Stop.

Claude Code will call this script before every exit attempt. If the script
outputs a message and exits with code 2, Claude receives that message as a
new user turn and continues working.
"""

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate vault relative to this hook's location
# ---------------------------------------------------------------------------
# .claude/hooks/ralph_wiggum.py → project root is two dirs up
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
_VAULT       = _PROJECT_DIR / "vault"


def _count_md(folder: Path) -> list[str]:
    """Return list of .md file names, excluding .gitkeep placeholder."""
    if not folder.exists():
        return []
    return [f.name for f in folder.glob("*.md") if f.name != ".gitkeep"]


def main() -> None:
    needs_action = _count_md(_VAULT / "Needs_Action")
    approved     = _count_md(_VAULT / "Approved")

    # ── Read stdin (Claude Code passes session info as JSON) ──────────────
    try:
        raw = sys.stdin.read()
        session = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        session = {}

    # ── Nothing to do — let Claude stop ───────────────────────────────────
    if not needs_action and not approved:
        sys.exit(0)

    # ── Build re-injection message ─────────────────────────────────────────
    parts: list[str] = ["⟳ **Ralph Wiggum Loop** — pending work detected, continuing.\n"]

    if needs_action:
        parts.append(
            f"**{len(needs_action)} item(s) in vault/Needs_Action/** waiting to be processed:\n"
            + "\n".join(f"  - `{name}`" for name in needs_action[:5])
            + ("\n  - _(and more...)_" if len(needs_action) > 5 else "")
        )
        parts.append(
            "\nPlease run the vault-operator skill to process these items:\n"
            "1. Read `vault/Company_Handbook.md` for rules\n"
            "2. Process each item in `vault/Needs_Action/`\n"
            "3. Create Plans and Approval requests as needed\n"
            "4. Update `vault/Dashboard.md`"
        )

    if approved:
        parts.append(
            f"\n**{len(approved)} approved action(s) in vault/Approved/** waiting to execute:\n"
            + "\n".join(f"  - `{name}`" for name in approved[:5])
        )
        parts.append(
            "\nPlease run the approval-executor skill to dispatch these:\n"
            "```bash\n"
            "python .claude/skills/approval-executor/scripts/execute.py --vault ./vault\n"
            "```"
        )

    message = "\n".join(parts)
    print(message)
    sys.exit(2)  # block stop, re-inject message as new user turn


if __name__ == "__main__":
    main()
