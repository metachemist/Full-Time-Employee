#!/usr/bin/env python3
"""
Archive Done — moves old files from vault/Done/ to vault/Archive/.

Files older than --days (default: 90) are moved to vault/Archive/<YYYY-MM>/
to keep vault/Done/ fast and manageable. Nothing is deleted.

Usage:
    python scripts/archive_done.py --vault ./vault
    python scripts/archive_done.py --vault ./vault --days 30
    python scripts/archive_done.py --vault ./vault --dry-run

Recommended cron (monthly, 3 AM on the 1st):
    0 3 1 * * python /path/to/scripts/archive_done.py --vault /path/to/vault
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def archive_done(vault: Path, days: int = 90, dry_run: bool = False) -> dict:
    done_dir    = vault / "Done"
    archive_dir = vault / "Archive"

    if not done_dir.exists():
        print(f"vault/Done/ does not exist at {done_dir}")
        return {"archived": 0, "skipped": 0, "errors": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    files  = [f for f in done_dir.glob("*.md") if f.name != ".gitkeep"]

    archived = 0
    skipped  = 0
    errors   = 0

    for f in sorted(files, key=lambda p: p.stat().st_mtime):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            skipped += 1
            continue

        # Organise archive by year-month of the file's modification time
        month_dir = archive_dir / mtime.strftime("%Y-%m")

        if dry_run:
            print(f"  [DRY RUN] Would move: {f.name} → Archive/{mtime.strftime('%Y-%m')}/")
            archived += 1
            continue

        try:
            month_dir.mkdir(parents=True, exist_ok=True)
            dest = month_dir / f.name
            if dest.exists():
                dest = dest.with_name(f"{dest.stem}_{int(mtime.timestamp())}.md")
            f.rename(dest)
            print(f"  Archived: {f.name} → Archive/{mtime.strftime('%Y-%m')}/{dest.name}")
            archived += 1
        except OSError as exc:
            print(f"  ERROR moving {f.name}: {exc}", file=sys.stderr)
            errors += 1

    return {"archived": archived, "skipped": skipped, "errors": errors}


def _write_audit(vault: Path, result: dict, days: int, dry_run: bool) -> None:
    log_dir = vault / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    entry = {
        "timestamp": _ts(),
        "event":     "archive_done",
        "source":    "archive_done.py",
        "dry_run":   dry_run,
        "days":      days,
        **result,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive old vault/Done/ files to vault/Archive/<YYYY-MM>/."
    )
    parser.add_argument("--vault",   default="./vault",
                        help="Path to Obsidian vault (default: ./vault)")
    parser.add_argument("--days",    type=int, default=90,
                        help="Archive files older than this many days (default: 90)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without moving any files")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.exists():
        print(f"ERROR: Vault not found: {vault}", file=sys.stderr)
        sys.exit(1)

    label = "[DRY RUN] " if args.dry_run else ""
    print(f"{label}Archiving Done/ files older than {args.days} days…")
    result = archive_done(vault, days=args.days, dry_run=args.dry_run)

    print(f"\nSummary: archived={result['archived']}  "
          f"skipped={result['skipped']}  errors={result['errors']}")

    if not args.dry_run:
        _write_audit(vault, result, args.days, args.dry_run)

    sys.exit(1 if result["errors"] else 0)


if __name__ == "__main__":
    main()
