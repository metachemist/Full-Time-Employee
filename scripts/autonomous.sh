#!/usr/bin/env bash
# autonomous.sh — Run Claude Code in Ralph Wiggum autonomous mode.
#
# Sets the .autonomous_mode flag before invoking Claude, causing the
# Ralph Wiggum stop hook to re-inject the vault-operator prompt until
# all items in vault/Needs_Action/ and vault/Approved/ are processed.
#
# Usage:
#   bash scripts/autonomous.sh
#   bash scripts/autonomous.sh "Custom task prompt here"
#
# The flag is automatically removed by the hook when work is complete.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FLAG="$PROJECT_DIR/.claude/hooks/.autonomous_mode"

DEFAULT_PROMPT="Process all pending vault work:
1. Read vault/Company_Handbook.md for rules of engagement
2. Process each item in vault/Needs_Action/ using the vault-operator skill
3. Create Plans in vault/Plans/ and approval requests in vault/Pending_Approval/ as needed
4. Run the approval-executor for any files in vault/Approved/
5. Move completed items to vault/Done/
6. Update vault/Dashboard.md when finished"

PROMPT="${1:-$DEFAULT_PROMPT}"

echo "[ralph-wiggum] Enabling autonomous mode → $FLAG"
touch "$FLAG"

echo "[ralph-wiggum] Invoking Claude Code..."
cd "$PROJECT_DIR"
claude -p "$PROMPT"

# Flag is cleaned up by the hook itself when done.
# Safety: remove it here too in case claude exited before the hook ran.
rm -f "$FLAG"
echo "[ralph-wiggum] Done."
