# Personal AI Employee

> *Your life and business on autopilot. Local-first, agent-driven, human-in-the-loop.*

A reference architecture and working implementation for building an autonomous **Digital FTE** (Full-Time Equivalent) — an AI agent that proactively manages personal and business affairs 24/7 using Claude Code as the reasoning engine and Obsidian as the shared memory layer.

---

## How It Works

The system follows a continuous **Perception → Reasoning → Action** loop:

```
External Sources (files, email, WhatsApp, bank)
         ↓
Watchers (Python) → vault/Needs_Action/  ← shared memory
         ↓
Claude Code reads vault, creates Plans
         ↓
Human reviews vault/Pending_Approval/  →  moves to /Approved/
         ↓
MCP Servers execute actions (email, browser, payments)
         ↓
Results logged → vault/Done/  +  vault/Logs/
```

The **Obsidian vault** is the single source of truth — every watcher, every Claude session, and every human review happens through plain Markdown files in that folder.

---

## Repository Layout

```
.claude/skills/
  browsing-with-playwright/   # Browser automation via Playwright MCP (22 tools)
  vault-operator/             # Skill: Claude reads/writes the vault

vault/                        # Obsidian vault (open with Obsidian)
  Dashboard.md                # Live status — check this daily
  Company_Handbook.md         # Autonomy rules — Claude reads before every action
  Inbox/                      # Drop files here; watcher picks them up
  Needs_Action/               # Watcher-created task files for Claude to process
  Plans/                      # Claude writes step-by-step plans here
  Pending_Approval/           # Claude writes here when human sign-off is needed
  Done/                       # Completed tasks land here
  Logs/                       # Append-only JSON audit log (one file per day)

watchers/
  base_watcher.py             # Abstract base class for all watcher scripts
  filesystem_watcher.py       # Watches vault/Inbox/ and creates Needs_Action entries
  requirements.txt
```

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| [Claude Code](https://claude.ai/code) | Latest | Reasoning engine |
| [Obsidian](https://obsidian.md) | v1.10.6+ | Vault GUI |
| Python | 3.13+ | Watcher scripts |
| Node.js | v24+ LTS | MCP servers |

---

## Quick Start

### 1. Open the vault in Obsidian

Point Obsidian at the `vault/` directory to get the full dashboard GUI.

### 2. Start the file system watcher

```bash
cd watchers
pip install -r requirements.txt
python filesystem_watcher.py ../vault
```

Drop any file into `vault/Inbox/` — the watcher instantly creates a structured action item in `vault/Needs_Action/`.

Keep the watcher alive across reboots with PM2:

```bash
pm2 start watchers/filesystem_watcher.py --interpreter python3 -- vault
pm2 save && pm2 startup
```

### 3. Run Claude on the vault

Open Claude Code in this directory and invoke the vault-operator skill:

```
/vault-operator
```

Claude will:
1. Read `Company_Handbook.md` for its rules
2. Read `Dashboard.md` for current state
3. Process every file in `Needs_Action/`
4. Create plans, take auto-approved actions, or write approval requests
5. Move completed items to `Done/` and update the Dashboard

### 4. Review approvals

For sensitive actions (email, payments, new contacts), Claude writes a request file to `vault/Pending_Approval/`. Move the file to `vault/Approved/` to proceed or `vault/Rejected/` to cancel.

---

## Autonomy Rules (summary)

Defined in full in `vault/Company_Handbook.md`.

| Action | Autonomous? |
|--------|-------------|
| Read vault, create plans, update Dashboard | ✅ Yes |
| Move files within vault | ✅ Yes |
| Send email / message | ❌ Needs approval |
| Any financial action | ❌ Needs approval |
| Contact new people | ❌ Needs approval |
| Delete files | ❌ Needs approval |

---

## Browser Automation (Playwright MCP)

The `browsing-with-playwright` skill connects Claude to a Playwright MCP server exposing 22 browser tools — navigate, click, fill forms, take screenshots, evaluate JavaScript, and more.

```bash
# From .claude/skills/browsing-with-playwright/
bash scripts/start-server.sh        # start on port 8808
python3 scripts/verify.py           # confirm it's ready
bash scripts/stop-server.sh         # stop
```

See `.claude/skills/browsing-with-playwright/references/playwright-tools.md` for the full tool reference.

---

## Hackathon Tiers

| Tier | Status | Description |
|------|--------|-------------|
| **Bronze** | ✅ Done | Vault structure, file system watcher, vault-operator skill |
| **Silver** | Planned | Gmail + WhatsApp watchers, LinkedIn posting, email MCP, HITL workflow |
| **Gold** | Planned | Full cross-domain integration, Odoo accounting, weekly CEO briefing, Ralph Wiggum loop |
| **Platinum** | Planned | Always-on cloud agent, cloud/local split, Syncthing vault sync |

---

## Audit Log

Every Claude action is appended to `vault/Logs/YYYY-MM-DD.json` as a single JSON object per line:

```json
{"timestamp": "2026-02-19T10:00:00", "action": "processed file drop", "source_file": "FILE_report_2026-02-19.md", "result": "success"}
```

Retain logs for a minimum of 90 days.

---

## Security Notes

- **Never commit credentials.** Use environment variables or your OS keychain.
- **Vault sync excludes secrets.** If syncing via Git or Syncthing, ensure `.env`, token files, and WhatsApp session data are in `.gitignore`.
- **Payments always need approval.** The handbook enforces this; do not relax it without careful consideration.
