"""
File System Watcher — monitors vault/Inbox/ for new files and creates
action items in vault/Needs_Action/ for Claude to process.

Usage:
    python filesystem_watcher.py <vault_path>

Example:
    python filesystem_watcher.py ../vault

The watcher runs until interrupted (Ctrl+C). Keep it alive with PM2:
    pm2 start filesystem_watcher.py --interpreter python3 -- ../vault
    pm2 save && pm2 startup
"""

import sys
import time
import argparse
import uuid

from datetime import datetime
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from base_watcher import BaseWatcher

# File extensions/prefixes to silently ignore (OS temp files, etc.)
_IGNORE_PREFIXES = (".", "~")
_IGNORE_SUFFIXES = (".tmp", ".part", ".crdownload")


class _InboxHandler(FileSystemEventHandler):
    """Passes watchdog on_created events to FileSystemWatcher."""

    def __init__(self, watcher: "FileSystemWatcher"):
        self.watcher = watcher

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name.startswith(_IGNORE_PREFIXES) or path.suffix in _IGNORE_SUFFIXES:
            return
        self.watcher.logger.info(f"New file detected: {path.name}")
        action_path = self.watcher.create_action_file(path)
        self.watcher.logger.info(f"Created action file: {action_path.name}")


class FileSystemWatcher(BaseWatcher):
    """
    Event-driven watcher that monitors vault/Inbox/ using watchdog.
    Creates a Needs_Action entry for every new file dropped in Inbox.
    """

    def __init__(self, vault_path: str):
        super().__init__(vault_path, check_interval=0)
        self.inbox = self.vault_path / "Inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)

    # BaseWatcher requires this method; not used in event-driven mode.
    def check_for_updates(self) -> list:
        return []

    def create_action_file(self, file_path: Path) -> Path:
        """Build a Needs_Action .md file describing the dropped file."""
        timestamp = datetime.now()
        name_upper = file_path.name.upper()

        # ── Typed dispatch: BRIEFING_*.md ─────────────────────────────────────
        if name_upper.startswith("BRIEFING_"):
            return self._create_briefing_action(file_path, timestamp)

        # ── Generic file-drop ─────────────────────────────────────────────────
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in file_path.stem)
        filename = f"FILE_{safe_stem}_{timestamp.strftime('%Y-%m-%d_%H%M%S')}.md"
        action_path = self.needs_action / filename

        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0

        trace_id = str(uuid.uuid4())
        content = f"""\
---
type: file_drop
original_name: {file_path.name}
source_path: {file_path}
size_bytes: {size}
received: {timestamp.isoformat()}
status: pending
trace_id: {trace_id}
---

## New File: {file_path.name}

A new file has been dropped into the Inbox folder.

**Details**
- Size: {size:,} bytes
- Received: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}

## Suggested Actions
- [ ] Review file contents
- [ ] Determine appropriate response or processing step
- [ ] Move to /Done when complete
"""
        action_path.write_text(content, encoding="utf-8")
        return action_path

    def _create_briefing_action(self, file_path: Path, timestamp: datetime) -> Path:
        """Create a typed briefing_request action from a BRIEFING_*.md inbox file."""
        # Determine scope from filename: BRIEFING_DAILY_*.md or BRIEFING_WEEKLY_*.md
        stem_upper = file_path.stem.upper()
        if "WEEKLY" in stem_upper:
            scope = "weekly"
        else:
            scope = "daily"

        date_str = timestamp.strftime("%Y-%m-%d")
        filename = f"BRIEFING_{scope.upper()}_{date_str}_{timestamp.strftime('%H%M%S')}.md"
        action_path = self.needs_action / filename

        content = f"""\
---
type: briefing_request
scope: {scope}
original_name: {file_path.name}
received: {timestamp.isoformat()}
status: pending
---

## CEO Briefing Requested

A {scope} briefing has been triggered by cron.

**Scope:** {scope} ({"last 24 hours" if scope == "daily" else "last 7 days"})
**Triggered:** {timestamp.strftime('%Y-%m-%d %H:%M:%S')}

## Required Action

Run the CEO briefing generator and write the output to `vault/Briefings/`:

```bash
python .claude/skills/ceo-briefing/scripts/generate_briefing.py \\
    --vault vault \\
    --scope {scope}
```

This is **auto-approved** (read-only — no external action). Move this file to `/Done` after the briefing is generated.
"""
        action_path.write_text(content, encoding="utf-8")
        # Move the Inbox trigger to Done so it doesn't re-trigger
        done_dir = self.vault_path / "Done"
        done_dir.mkdir(parents=True, exist_ok=True)
        dest = done_dir / f"INBOX_{file_path.name}"
        try:
            file_path.rename(dest)
        except OSError:
            pass  # Inbox file may have already been moved
        return action_path

    def run(self):
        self.logger.info(f"Watching Inbox : {self.inbox}")
        self.logger.info(f"Action files → : {self.needs_action}")

        handler = _InboxHandler(self)
        observer = Observer()
        observer.schedule(handler, str(self.inbox), recursive=False)
        observer.start()

        _last_ping = 0.0
        _PING_INTERVAL = 60  # seconds

        try:
            while True:
                time.sleep(1)
                now = time.time()
                if now - _last_ping >= _PING_INTERVAL:
                    self._ping_healthcheck()
                    _last_ping = now
        except KeyboardInterrupt:
            self.logger.info("Shutting down.")
        finally:
            observer.stop()
            observer.join()


def main():
    parser = argparse.ArgumentParser(
        description="Watch vault/Inbox for new files and create Needs_Action entries."
    )
    parser.add_argument(
        "vault",
        help="Path to the Obsidian vault directory (e.g. ../vault)",
    )
    args = parser.parse_args()

    watcher = FileSystemWatcher(args.vault)
    watcher.run()


if __name__ == "__main__":
    main()
