# Personal AI Employee — Silver Tier

> *Your life and business on autopilot. Local-first, agent-driven, human-in-the-loop.*

A working implementation of an autonomous **Digital FTE** (Full-Time Equivalent) — an AI agent that proactively manages personal and business communications 24/7 using Claude Code as the reasoning engine and Obsidian as the shared memory layer.

---

## How It Works

The system follows a continuous **Perception → Reasoning → Action** loop:

```
Gmail / WhatsApp / LinkedIn / Files
         ↓
Watchers (Python) → vault/Needs_Action/
         ↓
Planning Engine → vault/Plans/ + vault/Pending_Approval/
         ↓
Human reviews → moves to vault/Approved/
         ↓
Approval Executor → sends email / WhatsApp / LinkedIn post
         ↓
vault/Done/ + vault/Logs/  ← audit trail
```

The **Obsidian vault** is the single source of truth. Every watcher, every Claude session, and every human decision happens through plain Markdown files in the `vault/` directory.

---

## Tier Status

| Tier | Status | Description |
|------|--------|-------------|
| **Bronze** | ✅ Done | Vault structure, filesystem watcher, vault-operator skill |
| **Silver** | ✅ Done | Gmail + WhatsApp + LinkedIn watchers, planning engine, email/WhatsApp/LinkedIn actions, HITL workflow, Ralph Wiggum loop, scheduling |
| **Gold** | Planned | Odoo accounting, weekly CEO briefing, full cross-domain integration |
| **Platinum** | Planned | Always-on cloud agent, cloud/local split, Syncthing vault sync |

---

## Repository Layout

```
.claude/
  hooks/
    ralph_wiggum.py         # Stop hook — re-injects task if work remains
  skills/
    vault-operator/         # Read/write vault, process tasks, update dashboard
    browsing-with-playwright/ # Browser automation (22 Playwright tools)
    gmail-sender/           # Send approved emails via Gmail API
    whatsapp-sender/        # Send approved WhatsApp messages via Playwright
    linkedin-poster/        # Publish approved LinkedIn posts via Playwright
    approval-executor/      # Dispatch all approved actions to correct sender
  settings.local.json       # Permissions + Ralph Wiggum hook registration

vault/
  Dashboard.md              # Live status — check this daily
  Company_Handbook.md       # Autonomy rules — Claude reads before every action
  Inbox/                    # Drop files here; filesystem watcher picks them up
  Needs_Action/             # Watcher-generated task files for Claude to process
  Plans/                    # Claude writes step-by-step plans here
  Pending_Approval/         # Approval requests waiting for human decision
  Approved/                 # Move files here to authorise an action
  Rejected/                 # Move files here to cancel an action
  Done/                     # Completed tasks land here
  Logs/                     # Append-only JSONL audit log (one file per day)

watchers/
  base_watcher.py           # Abstract base: state persistence, retries, logging
  filesystem_watcher.py     # Watches vault/Inbox/, creates Needs_Action entries
  gmail_watcher.py          # Polls Gmail API (unread+important) → Needs_Action
  whatsapp_watcher.py       # Scrapes WhatsApp Web (Playwright) → Needs_Action
  linkedin_watcher.py       # Scrapes LinkedIn DMs + connections → Needs_Action
  auth_linkedin.py          # One-time LinkedIn session setup
  requirements.txt          # Python deps

orchestrator/
  planning_engine.py        # Scans Needs_Action, classifies, writes Plans + Approvals

cron/
  crontab.example           # Cron schedule for daily briefings, weekly reviews

ecosystem.config.js         # PM2 config: 4 watchers + planning engine + executor
```

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| [Claude Code](https://claude.ai/code) | Latest | Reasoning engine |
| [Obsidian](https://obsidian.md) | v1.10.6+ | Vault GUI |
| Python | 3.11+ | Watchers + orchestrator |
| Node.js | v18+ LTS | Playwright MCP server |
| PM2 | Latest | Process management (`npm install -g pm2`) |

---

## Quick Start

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -r watchers/requirements.txt
.venv/bin/playwright install chromium
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env and fill in:
#   GMAIL_CREDENTIALS=/absolute/path/to/credentials.json
#   WHATSAPP_SESSION_PATH=/home/you/.sessions/whatsapp
#   LINKEDIN_SESSION_PATH=/home/you/.sessions/linkedin
```

**Gmail:** Download `credentials.json` from [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials (OAuth 2.0).

**WhatsApp session:** Run once with a visible browser to scan the QR code:
```bash
WHATSAPP_HEADLESS=false python watchers/whatsapp_watcher.py ./vault
# Scan QR code in the browser, then Ctrl+C once logged in
```

**LinkedIn session:** Run the auth helper:
```bash
python watchers/auth_linkedin.py
# Log in with email + password (not Google Sign-In)
```

### 3. Start all processes with PM2

```bash
pm2 start ecosystem.config.js
pm2 save && pm2 startup           # survive reboots
pm2 logs                          # tail all logs
```

This starts:
- `watcher-filesystem` — monitors vault/Inbox/
- `watcher-gmail` — polls Gmail every 2 minutes
- `watcher-whatsapp` — scrapes WhatsApp Web every 30 seconds
- `watcher-linkedin` — scrapes LinkedIn every 5 minutes
- `planning-engine` — processes Needs_Action every 30 seconds
- `approval-executor` — dispatches approved actions every 30 seconds

### 4. Open the vault in Obsidian

Point Obsidian at the `vault/` directory. The Dashboard.md shows live counts.

### 5. Set up cron (for scheduled briefings)

```bash
# Edit cron/crontab.example: replace /path/to/full_time_employee with your real path
sed -i 's|/path/to/full_time_employee|'$(pwd)'|g' cron/crontab.example
crontab cron/crontab.example
```

---

## Daily Workflow

### Reviewing Approvals

When a watcher picks up an email, WhatsApp message, or LinkedIn connection:

1. The planning engine creates two files:
   - `vault/Plans/PLAN_*.md` — context, risk assessment, draft reply
   - `vault/Pending_Approval/APPROVAL_*.md` — ready-to-execute action

2. **You review** the approval file in Obsidian

3. **To approve:** Move the file to `vault/Approved/`
4. **To reject:** Move the file to `vault/Rejected/`

5. The approval executor picks it up within 30 seconds and:
   - Sends the email / WhatsApp message / LinkedIn post
   - Moves the file to `vault/Done/`
   - Writes an audit log entry

### Posting on LinkedIn

To create a sales or business post:

```
# In Claude Code:
/linkedin-poster

# Claude will:
# 1. Ask what you want to post about
# 2. Draft content using a sales framework
# 3. Write APPROVAL_SEND_LINKEDIN_POST_*.md to vault/Pending_Approval/
# 4. You move it to vault/Approved/ to publish
```

---

## Ralph Wiggum Loop

When Claude Code is running interactively and tries to exit, the Ralph Wiggum stop hook (`.claude/hooks/ralph_wiggum.py`) checks for:

- Items in `vault/Needs_Action/` that haven't been processed
- Items in `vault/Approved/` waiting to be executed

If work remains, it blocks Claude's exit and re-injects the task — Claude keeps working until the queue is empty.

---

## Agent Skills

All actions are implemented as loadable Claude Code skills:

| Skill | Trigger |
|-------|---------|
| `vault-operator` | Process vault tasks, update dashboard |
| `gmail-sender` | Send approved email after HITL approval |
| `whatsapp-sender` | Send approved WhatsApp message |
| `linkedin-poster` | Draft + publish LinkedIn post |
| `approval-executor` | Batch-dispatch all items in vault/Approved/ |
| `browsing-with-playwright` | General browser automation |

---

## Autonomy Rules

Defined in full in `vault/Company_Handbook.md`.

| Action | Autonomous? |
|--------|-------------|
| Read vault, create plans, update Dashboard | ✅ Yes |
| Move files within vault | ✅ Yes |
| Send email / message / post | ❌ Needs approval |
| Any financial action | ❌ Needs approval |
| Contact new people | ❌ Needs approval |
| Delete files | ❌ Needs approval |

---

## Security Notes

- **Never commit credentials.** Use `.env` (already in `.gitignore`) or your OS keychain.
- **Sessions stay local.** WhatsApp and LinkedIn session data is excluded from git.
- **Payments always need approval.** The handbook enforces this — do not relax it.
- **Audit everything.** Every action is logged to `vault/Logs/YYYY-MM-DD.jsonl`. Retain 90 days minimum.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No files in Needs_Action | Check `pm2 logs watcher-gmail` / `watcher-whatsapp` |
| Planning engine not running | `pm2 restart planning-engine` |
| WhatsApp session expired | `WHATSAPP_HEADLESS=false python watchers/whatsapp_watcher.py ./vault` |
| LinkedIn session expired | `python watchers/auth_linkedin.py` |
| Gmail token expired | Run `python watchers/gmail_watcher.py ./vault` once to re-auth |
| Approval not executing | Check `pm2 logs approval-executor`; verify file is in `vault/Approved/` not `Pending_Approval/` |
| Playwright browser crash | `bash .claude/skills/browsing-with-playwright/scripts/stop-server.sh && bash ...start-server.sh` |
