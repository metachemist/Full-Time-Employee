#!/usr/bin/env bash
# ==============================================================================
# Enable Audit Log Integrity — makes vault/Logs/ JSONL files append-only.
#
# Uses Linux chattr +a to set the append-only attribute on existing log files.
# New files created by the watchers will NOT automatically inherit this —
# re-run this script periodically (e.g. via cron) to lock new log files.
#
# IMPORTANT: Requires root (or CAP_LINUX_IMMUTABLE) to set chattr +a.
#            Run as: sudo bash scripts/enable_audit_integrity.sh
#
# Alternative (no root required): configure remote syslog so logs are shipped
# to a separate host. See docs/audit-integrity.md for details.
#
# Usage:
#   sudo bash scripts/enable_audit_integrity.sh [vault_path]
#   sudo bash scripts/enable_audit_integrity.sh ./vault --dry-run
# ==============================================================================

set -euo pipefail

VAULT="${1:-./vault}"
DRY_RUN="${2:-}"
LOGS_DIR="${VAULT}/Logs"

if [[ ! -d "${LOGS_DIR}" ]]; then
  echo "ERROR: Logs directory not found: ${LOGS_DIR}" >&2
  exit 1
fi

if [[ "${DRY_RUN}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would apply chattr +a to all .jsonl files in: ${LOGS_DIR}"
  find "${LOGS_DIR}" -name "*.jsonl" -print | while read -r f; do
    attrs=$(lsattr "${f}" 2>/dev/null | awk '{print $1}' || echo "?")
    echo "  ${attrs}  ${f}"
  done
  echo ""
  echo "Re-run without --dry-run (as root) to apply."
  exit 0
fi

# Check for root
if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: This script must be run as root (chattr requires CAP_LINUX_IMMUTABLE)." >&2
  echo "  sudo bash scripts/enable_audit_integrity.sh ${VAULT}" >&2
  exit 1
fi

# Check chattr is available
if ! command -v chattr &>/dev/null; then
  echo "ERROR: chattr not found. Install e2fsprogs: apt-get install e2fsprogs" >&2
  exit 1
fi

echo "Applying chattr +a (append-only) to audit logs in: ${LOGS_DIR}"
echo ""

count=0
errors=0

find "${LOGS_DIR}" -name "*.jsonl" | while read -r f; do
  if chattr +a "${f}" 2>/dev/null; then
    attrs=$(lsattr "${f}" 2>/dev/null | awk '{print $1}')
    echo "  OK [${attrs}]  ${f}"
    ((count++)) || true
  else
    echo "  FAIL        ${f}" >&2
    ((errors++)) || true
  fi
done

echo ""
echo "Done. Log files are now append-only."
echo "To verify: lsattr ${LOGS_DIR}/*.jsonl"
echo ""
echo "NOTE: Run this script daily (via cron) to protect new log files:"
echo "  0 0 * * * root bash $(realpath "$0") $(realpath "${VAULT}")"
echo ""
echo "To REMOVE append-only (e.g. for manual rotation):"
echo "  sudo chattr -a ${LOGS_DIR}/*.jsonl"
