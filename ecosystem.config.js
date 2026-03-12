/**
 * PM2 Ecosystem Config — AI Employee Watchers
 *
 * Prerequisites:
 *   npm install -g pm2
 *   cd /path/to/full_time_employee
 *
 * Usage:
 *   pm2 start ecosystem.config.js      # start all watchers
 *   pm2 stop all                       # stop all
 *   pm2 restart all                    # restart all
 *   pm2 logs                           # tail logs for all watchers
 *   pm2 save && pm2 startup            # survive system reboots
 *
 * Update VAULT_PATH and WATCHERS_DIR below before use.
 */

const path = require("path");

// ── Edit these two paths ────────────────────────────────────────────────────
const VAULT_PATH    = path.resolve(__dirname, "vault");
const WATCHERS_DIR  = path.resolve(__dirname, "watchers");
// ────────────────────────────────────────────────────────────────────────────

module.exports = {
  apps: [
    // ── File System Watcher ────────────────────────────────────────────────
    {
      name:        "watcher-filesystem",
      script:      path.join(WATCHERS_DIR, "filesystem_watcher.py"),
      interpreter: "python3",
      args:        VAULT_PATH,
      cwd:         WATCHERS_DIR,
      watch:       false,
      autorestart: true,
      max_restarts: 9999,
      min_uptime:  "10s",
      exp_backoff_restart_delay: 100,
      // Health: restart if process eats more than 256 MB (filesystem watcher is lightweight)
      max_memory_restart: "256M",
      kill_timeout:       5000,   // ms to wait for graceful SIGTERM before SIGKILL
      restart_delay:      4000,   // ms to wait between crash restarts
      env: {
        PYTHONUNBUFFERED: "1",
      },
      log_file:    path.resolve(__dirname, "logs", "pm2-filesystem.log"),
      error_file:  path.resolve(__dirname, "logs", "pm2-filesystem-error.log"),
      merge_logs:  true,
    },

    // ── Gmail Watcher ──────────────────────────────────────────────────────
    {
      name:        "watcher-gmail",
      script:      path.join(WATCHERS_DIR, "gmail_watcher.py"),
      interpreter: "python3",
      args:        VAULT_PATH,
      cwd:         WATCHERS_DIR,
      watch:       false,
      autorestart: true,
      max_restarts: 9999,
      min_uptime:  "10s",
      exp_backoff_restart_delay: 100,
      max_memory_restart: "256M",
      kill_timeout:       5000,
      restart_delay:      4000,
      env_file:    path.resolve(__dirname, ".env"),
      env: {
        PYTHONUNBUFFERED: "1",
      },
      log_file:    path.resolve(__dirname, "logs", "pm2-gmail.log"),
      error_file:  path.resolve(__dirname, "logs", "pm2-gmail-error.log"),
      merge_logs:  true,
    },

    // ── Approval Executor (Phase 2 — HITL dispatcher) ─────────────────────
    {
      name:        "approval-executor",
      script:      path.resolve(__dirname, ".claude", "skills", "approval-executor", "scripts", "execute.py"),
      interpreter: "python3",
      args:        ["--vault", VAULT_PATH, "--loop", "--interval", "30"],
      cwd:         path.resolve(__dirname),
      watch:       false,
      autorestart: true,
      max_restarts: 9999,
      min_uptime:  "10s",
      exp_backoff_restart_delay: 100,
      // Playwright (Chromium) can spike memory; allow up to 1 GB before restart
      max_memory_restart: "1G",
      kill_timeout:       10000,  // allow 10s for graceful shutdown (Playwright cleanup)
      restart_delay:      4000,
      listen_timeout:     15000,  // ms to wait for process to be "ready"
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH:       path.resolve(__dirname),
      },
      log_file:    path.resolve(__dirname, "logs", "pm2-executor.log"),
      error_file:  path.resolve(__dirname, "logs", "pm2-executor-error.log"),
      merge_logs:  true,
    },

    // ── Planning Engine (Phase 2) ──────────────────────────────────────────
    {
      name:        "planning-engine",
      script:      path.resolve(__dirname, "orchestrator", "planning_engine.py"),
      interpreter: "python3",
      args:        ["--vault", VAULT_PATH, "--loop", "--interval", "30"],
      cwd:         path.resolve(__dirname),
      watch:       false,
      autorestart: true,
      max_restarts: 9999,
      min_uptime:  "10s",
      exp_backoff_restart_delay: 100,
      max_memory_restart: "512M",
      kill_timeout:       5000,
      restart_delay:      4000,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH:       path.resolve(__dirname),
      },
      log_file:    path.resolve(__dirname, "logs", "pm2-planning.log"),
      error_file:  path.resolve(__dirname, "logs", "pm2-planning-error.log"),
      merge_logs:  true,
    },
  ],
};
