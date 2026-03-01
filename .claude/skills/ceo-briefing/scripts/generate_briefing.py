#!/usr/bin/env python3
"""
CEO Briefing Generator — AI Employee Gold Tier skill script.

Reads vault state (Done, Pending_Approval, Plans, Failed, Logs, Dashboard)
and produces a structured executive briefing in vault/Briefings/.

Can be triggered by:
  - cron (writes a BRIEFING_*.md to vault/Inbox/ then runs this script)
  - planning engine (processes the Inbox trigger file)
  - direct invocation

Usage:
    python generate_briefing.py --vault /path/to/vault --scope daily
    python generate_briefing.py --vault /path/to/vault --scope weekly
    python generate_briefing.py --vault /path/to/vault --scope daily --dry-run
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


_PROJECT_DIR = Path(__file__).resolve().parents[3]  # project root
_DEFAULT_VAULT = _PROJECT_DIR / "vault"


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _count_files(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for f in folder.iterdir() if f.is_file() and f.suffix == ".md" and f.name != ".gitkeep")


def _list_files(folder: Path, limit: int = 20) -> list[str]:
    if not folder.exists():
        return []
    files = sorted(
        (f for f in folder.iterdir() if f.is_file() and f.suffix == ".md" and f.name != ".gitkeep"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return [f.name for f in files[:limit]]


def _read_recent_log(vault: Path, since_hours: int = 24) -> list[dict]:
    """Read JSONL audit log entries from the last N hours."""
    cutoff = _now() - timedelta(hours=since_hours)
    entries = []
    logs_dir = vault / "Logs"
    if not logs_dir.exists():
        return entries

    # Check today's and yesterday's log files
    for delta in range(2):
        date_str = (_now() - timedelta(days=delta)).strftime("%Y-%m-%d")
        log_file = logs_dir / f"{date_str}.jsonl"
        if not log_file.exists():
            continue
        try:
            for line in log_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("timestamp", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts >= cutoff:
                            entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
        except OSError:
            continue

    return sorted(entries, key=lambda e: e.get("timestamp", ""), reverse=True)


def _summarise_log_entries(entries: list[dict]) -> list[str]:
    """Turn raw log entries into human-readable bullet points."""
    lines = []
    seen_events = {}
    for e in entries:
        event = e.get("event", "unknown")
        source = e.get("source", "")
        ts = e.get("timestamp", "")[:16].replace("T", " ")

        # Deduplicate repetitive watcher_started/stopped
        key = f"{source}:{event}"
        if event in ("watcher_started", "watcher_stopped"):
            if key in seen_events:
                continue
            seen_events[key] = True

        action = e.get("action", "")
        status = e.get("status", "")
        name = e.get("name", "") or e.get("subject", "") or e.get("file", "")

        if event == "item_created":
            lines.append(f"- `{ts}` [{source}] Created task: {name}")
        elif event == "action_executed":
            lines.append(f"- `{ts}` [executor] {action} → {status}")
        elif event == "watcher_error":
            err = e.get("error", "")[:80]
            lines.append(f"- `{ts}` [{source}] ERROR: {err}")
        elif event == "watcher_started":
            lines.append(f"- `{ts}` [{source}] started")

    return lines[:15]  # cap at 15 lines


def _read_pending_summaries(vault: Path) -> list[str]:
    """Extract one-line summaries from Pending_Approval files."""
    folder = vault / "Pending_Approval"
    summaries = []
    for f in _list_files(folder, limit=10):
        path = folder / f
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            # Find action type from frontmatter
            action = ""
            for line in lines[:10]:
                if line.startswith("action:"):
                    action = line.split(":", 1)[1].strip()
                    break
            # Find first heading after frontmatter
            in_front = True
            title = f
            for line in lines:
                if line.strip() == "---" and in_front:
                    in_front = not in_front
                    continue
                if not in_front and line.startswith("# "):
                    title = line.lstrip("# ").strip()
                    break
            summaries.append(f"- `{action or 'unknown'}` — {title} (`{f}`)")
        except OSError:
            summaries.append(f"- {f}")
    return summaries


def _read_failed_summaries(vault: Path) -> list[str]:
    folder = vault / "Failed"
    summaries = []
    for f in _list_files(folder, limit=10):
        summaries.append(f"- {f}")
    return summaries


def _read_open_plans(vault: Path) -> list[str]:
    folder = vault / "Plans"
    summaries = []
    for f in _list_files(folder, limit=10):
        summaries.append(f"- {f}")
    return summaries


def generate_briefing(vault: Path, scope: str, dry_run: bool = False) -> dict:
    now = _now()
    since_hours = 24 if scope == "daily" else 168  # 7 days for weekly

    # ── Counts ────────────────────────────────────────────────────────────────
    counts = {
        "needs_action":     _count_files(vault / "Needs_Action"),
        "plans":            _count_files(vault / "Plans"),
        "pending_approval": _count_files(vault / "Pending_Approval"),
        "approved":         _count_files(vault / "Approved"),
        "done":             _count_files(vault / "Done"),
        "failed":           _count_files(vault / "Failed"),
        "rejected":         _count_files(vault / "Rejected"),
    }

    # ── Recent activity ────────────────────────────────────────────────────────
    log_entries = _read_recent_log(vault, since_hours)
    log_bullets = _summarise_log_entries(log_entries)

    pending_bullets = _read_pending_summaries(vault)
    failed_bullets  = _read_failed_summaries(vault)
    plan_bullets    = _read_open_plans(vault)
    done_recent     = _list_files(vault / "Done", limit=10)

    # ── Recommended next steps ─────────────────────────────────────────────────
    recommendations = []
    if counts["pending_approval"] > 0:
        recommendations.append(
            f"Review and approve/reject {counts['pending_approval']} item(s) in `vault/Pending_Approval/`"
        )
    if counts["failed"] > 0:
        recommendations.append(
            f"Investigate {counts['failed']} failed action(s) in `vault/Failed/` — run `--retry-failed` if appropriate"
        )
    if counts["needs_action"] > 0:
        recommendations.append(
            f"{counts['needs_action']} item(s) in `vault/Needs_Action/` awaiting AI processing — trigger planning engine"
        )
    if not recommendations:
        recommendations.append("All clear — no immediate action required")

    # ── Compose briefing markdown ──────────────────────────────────────────────
    period_label = "Daily" if scope == "daily" else "Weekly"
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M UTC")
    since_label = f"last {since_hours}h"

    lines = [
        f"# {period_label} CEO Briefing — {date_str}",
        f"*Generated {time_str} • Scope: {since_label}*",
        "",
        "---",
        "",
        "## Headline Metrics",
        "",
        "| Status              | Count |",
        "|---------------------|-------|",
        f"| 📥 Needs Action     | {counts['needs_action']} |",
        f"| 📋 Open Plans       | {counts['plans']} |",
        f"| ⏳ Pending Approval | {counts['pending_approval']} |",
        f"| ✅ Approved (queued)| {counts['approved']} |",
        f"| ✔ Done              | {counts['done']} |",
        f"| ❌ Failed           | {counts['failed']} |",
        f"| 🚫 Rejected         | {counts['rejected']} |",
        "",
    ]

    # Done
    lines += ["## Actions Completed (Recent)", ""]
    if done_recent:
        for f in done_recent:
            lines.append(f"- {f}")
    else:
        lines.append("- *(none)*")
    lines.append("")

    # Pending approval
    lines += ["## Awaiting Your Approval", ""]
    if pending_bullets:
        lines += pending_bullets
    else:
        lines.append("- *(none)*")
    lines.append("")

    # Failed
    lines += ["## Failures to Review", ""]
    if failed_bullets:
        lines += failed_bullets
    else:
        lines.append("- *(none — all clear)*")
    lines.append("")

    # Open plans
    lines += ["## Open Plans", ""]
    if plan_bullets:
        lines += plan_bullets
    else:
        lines.append("- *(none)*")
    lines.append("")

    # Audit highlights
    lines += [f"## Audit Highlights ({since_label})", ""]
    if log_bullets:
        lines += log_bullets
    else:
        lines.append("- *(no log entries found)*")
    lines.append("")

    # Recommendations
    lines += ["## Recommended Next Steps", ""]
    for rec in recommendations:
        lines.append(f"1. {rec}")
    lines += [
        "",
        "---",
        f"*Briefing generated by AI Employee — {now.isoformat()}*",
    ]

    briefing_text = "\n".join(lines)
    word_count = len(briefing_text.split())

    if dry_run:
        return {
            "status":      "dry_run",
            "scope":       scope,
            "word_count":  word_count,
            "counts":      counts,
            "preview":     briefing_text[:400] + ("..." if len(briefing_text) > 400 else ""),
            "timestamp":   _ts(),
        }

    # ── Write briefing file ────────────────────────────────────────────────────
    briefings_dir = vault / "Briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    out_file = briefings_dir / f"BRIEFING_{scope.upper()}_{date_str}.md"
    out_file.write_text(briefing_text, encoding="utf-8")

    # ── Audit log ─────────────────────────────────────────────────────────────
    log_dir = vault / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{date_str}.jsonl"
    audit_entry = {
        "timestamp":   _ts(),
        "source":      "CeoBriefingGenerator",
        "event":       "briefing_generated",
        "scope":       scope,
        "output_file": str(out_file),
        "word_count":  word_count,
        "counts":      counts,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(audit_entry) + "\n")

    return {
        "status":      "generated",
        "scope":       scope,
        "output_file": str(out_file),
        "word_count":  word_count,
        "counts":      counts,
        "timestamp":   _ts(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CEO Briefing Generator — AI Employee Gold Tier script."
    )
    parser.add_argument(
        "--vault",
        default=str(_DEFAULT_VAULT),
        help=f"Path to vault directory (default: {_DEFAULT_VAULT})",
    )
    parser.add_argument(
        "--scope",
        choices=["daily", "weekly"],
        default="daily",
        help="Briefing scope: daily (24h) or weekly (7d) (default: daily)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.exists():
        print(json.dumps({"status": "error", "error": f"Vault not found: {vault}"}))
        sys.exit(1)

    result = generate_briefing(vault=vault, scope=args.scope, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("generated", "dry_run") else 1)


if __name__ == "__main__":
    main()
