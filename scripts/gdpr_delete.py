#!/usr/bin/env python3
"""
GDPR Delete — finds and removes all vault files containing a given email address.

Implements the "right to erasure" (GDPR Art. 17) for vault-stored PII.
Searches all vault subdirectories for markdown files referencing the target email,
lists them with --dry-run, and redacts/deletes with --confirm.

Usage:
    # Preview — list all matching files (safe, no changes)
    python scripts/gdpr_delete.py --vault ./vault --email user@example.com

    # Execute — redact content and move files to vault/Redacted/
    python scripts/gdpr_delete.py --vault ./vault --email user@example.com --confirm

    # Hard delete instead of redacting
    python scripts/gdpr_delete.py --vault ./vault --email user@example.com --confirm --hard-delete

All deletions are logged to vault/Logs/gdpr_deletions.jsonl.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# Vault subdirs to search (excludes Logs/ — audit trail must be preserved)
_SEARCH_DIRS = [
    "Needs_Action", "Plans", "Pending_Approval", "Approved",
    "Rejected", "Done", "Failed", "Briefings", "Archive",
]


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_matching_files(vault: Path, email: str) -> list[Path]:
    """Return all .md files in vault that contain the target email address."""
    pattern = re.compile(re.escape(email), re.IGNORECASE)
    matches = []
    for subdir in _SEARCH_DIRS:
        d = vault / subdir
        if not d.exists():
            continue
        for f in d.rglob("*.md"):
            try:
                if pattern.search(f.read_text(encoding="utf-8", errors="ignore")):
                    matches.append(f)
            except OSError:
                pass
    return sorted(matches)


def _redact_file(f: Path, email: str) -> str:
    """Replace all occurrences of email with [REDACTED] and return new content."""
    pattern = re.compile(re.escape(email), re.IGNORECASE)
    original = f.read_text(encoding="utf-8", errors="ignore")
    return pattern.sub("[REDACTED]", original)


def _log_deletion(vault: Path, email: str, files: list[Path],
                  action: str, hard_delete: bool) -> None:
    log_dir  = vault / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "gdpr_deletions.jsonl"
    entry = {
        "timestamp":   _ts(),
        "event":       "gdpr_deletion",
        "email":       email,
        "action":      action,
        "hard_delete": hard_delete,
        "files":       [str(f.relative_to(vault)) for f in files],
        "count":       len(files),
    }
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GDPR right-to-erasure tool — find and remove PII from the vault."
    )
    parser.add_argument("--vault",       default="./vault",
                        help="Path to Obsidian vault (default: ./vault)")
    parser.add_argument("--email",       required=True,
                        help="Email address to search for and remove")
    parser.add_argument("--confirm",     action="store_true",
                        help="Execute the deletion (default: dry-run preview only)")
    parser.add_argument("--hard-delete", action="store_true",
                        help="Permanently delete files instead of redacting and moving to vault/Redacted/")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.exists():
        print(f"ERROR: Vault not found: {vault}", file=sys.stderr)
        sys.exit(1)

    email = args.email.strip().lower()
    print(f"Searching vault for: {email}")
    print(f"Vault: {vault}")
    print()

    matches = _find_matching_files(vault, email)

    if not matches:
        print("No matching files found.")
        sys.exit(0)

    print(f"Found {len(matches)} file(s) containing '{email}':\n")
    for f in matches:
        rel = f.relative_to(vault)
        print(f"  {rel}")

    if not args.confirm:
        print(f"\n[DRY RUN] No changes made. Re-run with --confirm to execute deletion.")
        sys.exit(0)

    print()
    redacted_dir = vault / "Redacted"
    processed = []
    errors    = 0

    for f in matches:
        rel = f.relative_to(vault)
        try:
            if args.hard_delete:
                f.unlink()
                print(f"  DELETED:  {rel}")
            else:
                # Redact content and move to vault/Redacted/ preserving subdir structure
                new_content = _redact_file(f, email)
                dest_dir = redacted_dir / f.parent.relative_to(vault)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f.name
                if dest.exists():
                    dest = dest.with_name(f"{dest.stem}_{int(datetime.now().timestamp())}.md")
                dest.write_text(new_content, encoding="utf-8")
                f.unlink()
                print(f"  REDACTED: {rel} → Redacted/{dest.relative_to(redacted_dir)}")
            processed.append(f)
        except OSError as exc:
            print(f"  ERROR:    {rel}: {exc}", file=sys.stderr)
            errors += 1

    print(f"\nCompleted: {len(processed)} redacted/deleted, {errors} errors.")

    _log_deletion(
        vault, email, processed,
        action="hard_delete" if args.hard_delete else "redact_and_move",
        hard_delete=args.hard_delete,
    )
    print(f"Deletion logged to vault/Logs/gdpr_deletions.jsonl")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
