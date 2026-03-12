"""Base class for all AI Employee watcher scripts.

Provides:
  - Persistent state (processed IDs survive process restarts via JSON file)
  - Dual logging: console + rotating file at logs/watchers.log
  - with_retry decorator for transient-error resilience
  - Poll-based run loop with graceful KeyboardInterrupt shutdown
"""

import json
import os
import threading
import time
import logging
import logging.handlers
import urllib.request
from dataclasses import dataclass, field, asdict
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
# Structured log entry — enforces a consistent JSONL schema across all
# components (watchers, planning engine, executor).
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LogEntry:
    """Canonical JSONL audit log entry. All audit writes must go through this."""
    event:    str
    source:   str
    level:    str  = "INFO"
    trace_id: str  = ""
    timestamp: str = field(default_factory=_iso_now)
    # Arbitrary extra key/value pairs (merged at serialisation time)
    _extra:   dict = field(default_factory=dict, repr=False)

    def with_extra(self, **kwargs) -> "LogEntry":
        """Return a copy enriched with extra fields."""
        copy = LogEntry(
            event=self.event, source=self.source,
            level=self.level, trace_id=self.trace_id,
            timestamp=self.timestamp,
        )
        copy._extra = {**self._extra, **kwargs}
        return copy

    def to_dict(self) -> dict:
        d: dict = {
            "timestamp": self.timestamp,
            "level":     self.level,
            "source":    self.source,
            "event":     self.event,
        }
        if self.trace_id:
            d["trace_id"] = self.trace_id
        d.update(self._extra)
        return d

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ---------------------------------------------------------------------------
# Circuit breaker — prevents hammering a failing external API.
#
# States:
#   CLOSED    — normal operation; failures increment counter
#   OPEN      — API is down; calls rejected immediately
#   HALF_OPEN — recovery probe; one call allowed through
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Three-state circuit breaker for external API calls."""

    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name:              str   = "default",
        failure_threshold: int   = 5,
        recovery_timeout:  float = 60.0,
    ):
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self._state            = self.CLOSED
        self._failure_count    = 0
        self._opened_at: float | None = None
        self._lock             = threading.Lock()
        self._log              = logging.getLogger(f"CircuitBreaker[{name}]")

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._state != self.OPEN:
                return False
            elapsed = time.monotonic() - (self._opened_at or 0.0)
            if elapsed >= self.recovery_timeout:
                self._state = self.HALF_OPEN
                self._log.info(
                    f"Circuit '{self.name}': OPEN → HALF_OPEN (testing after {elapsed:.0f}s)"
                )
                return False
            return True

    def call(self, fn, *args, **kwargs):
        """Call fn through the circuit. Raises RuntimeError if circuit is OPEN."""
        if self.is_open:
            with self._lock:
                secs_left = self.recovery_timeout - (
                    time.monotonic() - (self._opened_at or 0.0)
                )
            raise RuntimeError(
                f"Circuit '{self.name}' is OPEN — "
                f"{max(0, secs_left):.0f}s until retry"
            )
        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            raise

    def _on_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._log.info(
                    f"Circuit '{self.name}': HALF_OPEN → CLOSED (recovered)"
                )
            self._state         = self.CLOSED
            self._failure_count = 0
            self._opened_at     = None

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._log.warning(
                f"Circuit '{self.name}': "
                f"failure {self._failure_count}/{self.failure_threshold} — {exc}"
            )
            if self._failure_count >= self.failure_threshold:
                self._state     = self.OPEN
                self._opened_at = time.monotonic()
                self._log.error(
                    f"Circuit '{self.name}': CLOSED → OPEN "
                    f"(will retry after {self.recovery_timeout}s)"
                )

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED (e.g. after manual remediation)."""
        with self._lock:
            self._state         = self.CLOSED
            self._failure_count = 0
            self._opened_at     = None
        self._log.info(f"Circuit '{self.name}': manually reset to CLOSED")


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
        """Persist processed IDs to disk atomically (write-then-replace)."""
        try:
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"processed_ids": sorted(self.processed_ids)}, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._state_file)
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

    def _write_audit(self, event: str, *, level: str = "INFO",
                     trace_id: str = "", **kwargs) -> None:
        """Append a structured LogEntry to vault/Logs/<today>.jsonl."""
        try:
            entry = LogEntry(
                event=event,
                source=self.__class__.__name__,
                level=level,
                trace_id=trace_id,
            ).with_extra(**kwargs)
            log_dir = self.vault_path / "Logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d.jsonl")
            with log_file.open("a", encoding="utf-8") as f:
                f.write(entry.to_jsonl() + "\n")
        except Exception as exc:
            self.logger.error(f"Failed to write audit log: {exc}")

    # ------------------------------------------------------------------
    # Dead-man switch — ping Healthchecks.io after each successful cycle
    # ------------------------------------------------------------------

    def _ping_healthcheck(self) -> None:
        """Ping HEALTHCHECK_URL env var (silently no-ops if unset or unreachable)."""
        url = os.environ.get("HEALTHCHECK_URL", "").strip()
        if not url:
            return
        try:
            urllib.request.urlopen(url, timeout=5)
        except Exception:
            pass  # Never let a failed ping crash the watcher

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
                self._ping_healthcheck()
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                self.logger.info("Shutdown requested — exiting cleanly.")
                self._write_audit("watcher_stopped", reason="KeyboardInterrupt")
                break
            except Exception as exc:
                self.logger.error(f"Unhandled error in run loop: {exc}", exc_info=True)
                self._write_audit("watcher_error", error=str(exc))
                time.sleep(self.check_interval)
