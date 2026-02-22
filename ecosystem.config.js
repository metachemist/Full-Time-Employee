/**
 * PM2 Ecosystem Config — AI Employee Silver Tier Watchers
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
      max_restarts: 10,
      restart_delay: 5000,
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
      max_restarts: 10,
      restart_delay: 15000,   // back off 15 s on crash (auth errors etc.)
      env_file:    path.resolve(__dirname, ".env"),
      env: {
        PYTHONUNBUFFERED: "1",
      },
      log_file:    path.resolve(__dirname, "logs", "pm2-gmail.log"),
      error_file:  path.resolve(__dirname, "logs", "pm2-gmail-error.log"),
      merge_logs:  true,
    },

    // ── WhatsApp Watcher ───────────────────────────────────────────────────
    {
      name:        "watcher-whatsapp",
      script:      path.join(WATCHERS_DIR, "whatsapp_watcher.py"),
      interpreter: "python3",
      args:        VAULT_PATH,
      cwd:         WATCHERS_DIR,
      watch:       false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 10000,
      env_file:    path.resolve(__dirname, ".env"),
      env: {
        PYTHONUNBUFFERED: "1",
        WHATSAPP_HEADLESS: "true",
      },
      log_file:    path.resolve(__dirname, "logs", "pm2-whatsapp.log"),
      error_file:  path.resolve(__dirname, "logs", "pm2-whatsapp-error.log"),
      merge_logs:  true,
    },

    // ── LinkedIn Watcher ───────────────────────────────────────────────────
    {
      name:        "watcher-linkedin",
      script:      path.join(WATCHERS_DIR, "linkedin_watcher.py"),
      interpreter: "python3",
      args:        VAULT_PATH,
      cwd:         WATCHERS_DIR,
      watch:       false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 15000,
      env_file:    path.resolve(__dirname, ".env"),
      env: {
        PYTHONUNBUFFERED: "1",
        LINKEDIN_HEADLESS: "true",
      },
      log_file:    path.resolve(__dirname, "logs", "pm2-linkedin.log"),
      error_file:  path.resolve(__dirname, "logs", "pm2-linkedin-error.log"),
      merge_logs:  true,
    },
  ],
};
