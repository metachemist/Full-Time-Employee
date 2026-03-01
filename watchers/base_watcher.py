"""Base class for all AI Employee watcher scripts.

Provides:
  - Persistent state (processed IDs survive process restarts via JSON file)
  - Dual logging: console + rotating file at logs/watchers.log
  - with_retry decorator for transient-error resilience
  - Poll-based run loop with graceful KeyboardInterrupt shutdown
"""

import json
import time
import logging
import logging.handlers
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from abc import ABC, abstractmethod

# ---------------------------------------------------------------------------
# Logging setup — console + rotating file (10 MB × 5 backups)
# ---------------------------------------------------------------------------

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _setup_logging() -> None:
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(fmt)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    rotating = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "watchers.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    rotating.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(console)
        root.addHandler(rotating)


_setup_logging()

# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
):
    """Exponential-backoff retry decorator for transient errors.

    Usage:
        @with_retry(max_attempts=3, base_delay=5.0)
        def fetch_data(self): ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            log = logging.getLogger(func.__qualname__)
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    log.warning(
                        f"Attempt {attempt}/{max_attempts} failed ({exc}). "
                        f"Retrying in {delay:.0f}s…"
                    )
                    time.sleep(delay)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# BaseWatcher
# ---------------------------------------------------------------------------


class BaseWatcher(ABC):
    """Abstract base for all AI Employee watchers.

    Subclasses must implement:
        check_for_updates() -> list[any]   — return new, unprocessed items
        create_action_file(item) -> Path   — write a .md to Needs_Action

    State persistence:
        Processed item IDs are written to watchers/.state/<ClassName>.state.json
        so duplicate events are skipped across process restarts.
    """

    def __init__(
        self,
        vault_path: str,
        check_interval: int = 60,
        state_filename: str | None = None,
    ):
        self.vault_path = Path(vault_path).resolve()
        self.needs_action = self.vault_path / "Needs_Action"
        self.check_interval = check_interval
        self.logger = logging.getLogger(self.__class__.__name__)

        # Persistent state directory
        state_dir = Path(__file__).parent / ".state"
        state_dir.mkdir(parents=True, exist_ok=True)
        fname = state_filename or f"{self.__class__.__name__}.state.json"
        self._state_file = state_dir / fname
        self.processed_ids: set[str] = self._load_state()

        self._ensure_dirs()

    # ------------------------------------------------------------------
    # Directory setup
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        self.needs_action.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> set[str]:
        """Load processed IDs from the JSON state file."""
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                ids = set(data.get("processed_ids", []))
                self.logger.debug(f"Loaded {len(ids)} processed IDs from state.")
                return ids
            except (json.JSONDecodeError, OSError) as exc:
                self.logger.warning(f"Could not load state file ({exc}); starting fresh.")
        return set()

    def _save_state(self) -> None:
        """Persist processed IDs to disk immediately."""
        try:
            self._state_file.write_text(
                json.dumps({"processed_ids": sorted(self.processed_ids)}, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self.logger.error(f"Could not save state file: {exc}")

    def _mark_processed(self, item_id: str) -> None:
        """Record that item_id has been handled and persist state."""
        self.processed_ids.add(item_id)
        self._save_state()

    def _is_processed(self, item_id: str) -> bool:
        return item_id in self.processed_ids

    # ------------------------------------------------------------------
    # Audit logging — writes JSONL entries to vault/Logs/<date>.jsonl
    # ------------------------------------------------------------------

    def _write_audit(self, event: str, **kwargs) -> None:
        """Append a structured audit event to vault/Logs/<today>.jsonl."""
        try:
            now = datetime.now(timezone.utc)
            log_dir = self.vault_path / "Logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / now.strftime("%Y-%m-%d.jsonl")
            entry = {
                "timestamp": now.isoformat(),
                "source": self.__class__.__name__,
                "event": event,
                **kwargs,
            }
            with log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:
            self.logger.error(f"Failed to write audit log: {exc}")

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def check_for_updates(self) -> list:
        """Return a list of new, unprocessed items to handle."""

    @abstractmethod
    def create_action_file(self, item) -> Path:
        """Write a structured .md file to Needs_Action and return its path."""

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Poll-based run loop. Override for event-driven watchers."""
        self.logger.info(
            f"Starting {self.__class__.__name__} (interval={self.check_interval}s)"
        )
        self._write_audit("watcher_started", interval=self.check_interval)
        while True:
            try:
                items = self.check_for_updates()
                for item in items:
                    try:
                        path = self.create_action_file(item)
                        self.logger.info(f"Created action file: {path.name}")
                        self._write_audit(
                            "item_created",
                            file=path.name,
                            item_id=item.get("id") if isinstance(item, dict) else None,
                        )
                    except Exception as exc:
                        self.logger.error(
                            f"Failed to create action file for {item!r}: {exc}",
                            exc_info=True,
                        )
                        self._write_audit(
                            "item_create_error",
                            error=str(exc),
                            item=str(item)[:200],
                        )
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                self.logger.info("Shutdown requested — exiting cleanly.")
                self._write_audit("watcher_stopped", reason="KeyboardInterrupt")
                break
            except Exception as exc:
                self.logger.error(f"Unhandled error in run loop: {exc}", exc_info=True)
                self._write_audit("watcher_error", error=str(exc))
                time.sleep(self.check_interval)
