"""Pytest configuration — adds project root and executor scripts to sys.path."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
EXECUTOR_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "approval-executor" / "scripts"

# Make orchestrator importable as a package
sys.path.insert(0, str(PROJECT_ROOT))
# Make execute.py importable directly by name
sys.path.insert(0, str(EXECUTOR_SCRIPTS))
