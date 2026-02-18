"""Base class for all AI Employee watcher scripts."""
import time
import logging
from pathlib import Path
from abc import ABC, abstractmethod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


class BaseWatcher(ABC):
    """
    Abstract base for watchers that monitor external sources and write
    action files to the vault's Needs_Action folder.
    """

    def __init__(self, vault_path: str, check_interval: int = 60):
        self.vault_path = Path(vault_path).resolve()
        self.needs_action = self.vault_path / "Needs_Action"
        self.check_interval = check_interval
        self.logger = logging.getLogger(self.__class__.__name__)
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.needs_action.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def check_for_updates(self) -> list:
        """Return a list of new items to process."""

    @abstractmethod
    def create_action_file(self, item) -> Path:
        """Create a .md file in Needs_Action and return its path."""

    def run(self):
        """Poll-based run loop. Override for event-driven watchers."""
        self.logger.info(f"Starting {self.__class__.__name__} (interval={self.check_interval}s)")
        while True:
            try:
                items = self.check_for_updates()
                for item in items:
                    path = self.create_action_file(item)
                    self.logger.info(f"Created action file: {path.name}")
            except KeyboardInterrupt:
                self.logger.info("Shutting down.")
                break
            except Exception as e:
                self.logger.error(f"Unhandled error: {e}", exc_info=True)
            time.sleep(self.check_interval)
