#!/usr/bin/env python3
"""
Secure Sessions — enforce tight filesystem permissions on Playwright session directories.

Playwright persistent contexts store browser cookies, tokens, and localStorage
in plain JSON/SQLite files on disk. Anyone with read access to those files can
impersonate authenticated accounts. This script locks them down to owner-only.

Applies:
  - Session directories: chmod 700  (owner rwx, nobody else)
  - Files inside:        chmod 600  (owner rw, nobody else)

Usage:
    python scripts/secure_sessions.py                     # default ~/.sessions/
    python scripts/secure_sessions.py --sessions-dir /custom/path
    python scripts/secure_sessions.py --dry-run           # preview only

Recommended: run once at setup and after each re-authentication.
"""

import argparse
import os
import stat
import sys
from pathlib import Path


_DEFAULT_SESSIONS = Path.home() / ".sessions"

_DIR_MODE  = stat.S_IRWXU                          # 0o700
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR           # 0o600


def _mode_str(mode: int) -> str:
    return oct(mode & 0o777)


def secure_sessions(sessions_dir: Path, dry_run: bool = False) -> dict:
    """Apply restrictive permissions to all session directories."""
    if not sessions_dir.exists():
        print(f"Sessions directory not found: {sessions_dir}")
        print("Nothing to do — create sessions first by running the auth scripts.")
        return {"dirs": 0, "files": 0, "errors": 0}

    dirs  = 0
    files = 0
    errors = 0
    label = "[DRY RUN] " if dry_run else ""

    for entry in sessions_dir.iterdir():
        if not entry.is_dir():
            continue

        # Secure the session subdirectory itself (e.g. ~/.sessions/twitter/)
        current_dir_mode = entry.stat().st_mode & 0o777
        if current_dir_mode != _DIR_MODE:
            print(f"  {label}chmod 700  {entry}  (was {_mode_str(current_dir_mode)})")
            if not dry_run:
                try:
                    os.chmod(entry, _DIR_MODE)
                    dirs += 1
                except OSError as exc:
                    print(f"  ERROR: {exc}", file=sys.stderr)
                    errors += 1
        else:
            print(f"  OK  700    {entry}")
            dirs += 1

        # Secure every file inside the session directory
        for f in entry.rglob("*"):
            if f.is_symlink() or not f.is_file():
                continue
            current_file_mode = f.stat().st_mode & 0o777
            if current_file_mode != _FILE_MODE:
                print(f"  {label}chmod 600  {f.relative_to(sessions_dir)}  (was {_mode_str(current_file_mode)})")
                if not dry_run:
                    try:
                        os.chmod(f, _FILE_MODE)
                        files += 1
                    except OSError as exc:
                        print(f"  ERROR: {exc}", file=sys.stderr)
                        errors += 1
            else:
                files += 1

    return {"dirs": dirs, "files": files, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restrict Playwright session file permissions to owner-only."
    )
    parser.add_argument(
        "--sessions-dir",
        default=str(_DEFAULT_SESSIONS),
        help=f"Path to sessions directory (default: {_DEFAULT_SESSIONS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying any files",
    )
    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir).expanduser().resolve()
    label = "[DRY RUN] " if args.dry_run else ""
    print(f"{label}Securing Playwright sessions at: {sessions_dir}")
    print()

    result = secure_sessions(sessions_dir, dry_run=args.dry_run)

    print()
    print(f"Summary: {result['dirs']} dir(s) | {result['files']} file(s) | {result['errors']} error(s)")

    if args.dry_run:
        print("\nRe-run without --dry-run to apply changes.")

    sys.exit(1 if result["errors"] else 0)


if __name__ == "__main__":
    main()
