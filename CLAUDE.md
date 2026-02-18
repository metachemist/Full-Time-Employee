# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a reference architecture and implementation guide for building autonomous "Digital FTE" (Full-Time Employee) agents using Claude Code as the reasoning engine. The main blueprint lives in `Personal AI Employee Hackathon 0_ Building Autonomous FTEs in 2026.md`. The only actively deployable component is the Playwright MCP browser automation skill in `.claude/skills/browsing-with-playwright/`.

## Playwright MCP Skill Commands

All commands run from `.claude/skills/browsing-with-playwright/`:

```bash
# Start Playwright MCP server (default port 8808)
bash scripts/start-server.sh [port]

# Verify server is running
python3 scripts/verify.py

# List available MCP tools
python3 scripts/mcp-client.py list --url http://localhost:8808

# Call a specific MCP tool
python3 scripts/mcp-client.py call -u http://localhost:8808 -t browser_snapshot -p '{}'

# Stop the server
bash scripts/stop-server.sh [port]
```

## Architecture

The system follows a **Perception → Reasoning → Action** loop:

```
External Sources (Gmail, WhatsApp, Bank, Files)
         ↓
Watchers (Python scripts) → /Needs_Action/ (Obsidian vault .md files)
         ↓
Claude Code (reads vault, generates plans in /Plans/)
         ↓
Human-in-the-Loop → /Pending_Approval/ → human moves to /Approved/
         ↓
MCP Servers execute actions (email, browser, payments, social)
         ↓
Results logged → /Done/
```

**Obsidian Vault** acts as the shared memory/GUI between all components. Markdown files with checkboxes serve as task state.

## Key Architectural Patterns

**Watcher Pattern**: Background Python scripts monitor external sources and write `.md` files to `/Needs_Action/` when events are detected. Watchers should run under a process manager (PM2 or supervisord) for reliability.

**Ralph Wiggum Loop**: A stop hook intercepts Claude's exit, checks whether the current task is complete (by detecting output in `/Done/` or a promise file), and re-injects the task prompt if not complete. This prevents the agent from stopping prematurely.

**Human-in-the-Loop (HITL)**: For sensitive actions (payments, new contacts, large emails), Claude writes a request file to `/Pending_Approval/` instead of acting directly. The orchestrator watches for human-moved files in `/Approved/` before triggering MCP execution.

**Company Handbook**: `/Company_Handbook.md` in the vault defines the agent's rules of engagement — what it can do autonomously vs. what requires approval. Edit this file to tune agent behavior.

## Bronze Tier Deliverables

The `vault/` directory is the Obsidian vault and `watchers/` contains the perception layer.

```
vault/
  Dashboard.md          # Live status — update after every Claude session
  Company_Handbook.md   # Autonomy rules — read before acting
  Inbox/                # Drop folder watched by filesystem_watcher.py
  Needs_Action/         # Watcher writes here; Claude reads and processes
  Plans/                # Claude writes PLAN_*.md files here
  Done/                 # Completed tasks moved here (never copied)
  Pending_Approval/     # Approval requests awaiting human review
  Logs/                 # Append-only JSON audit log (one file per day)

watchers/
  base_watcher.py       # Abstract base class for all watchers
  filesystem_watcher.py # Event-driven watcher (watchdog) for vault/Inbox
  requirements.txt      # pip deps: watchdog>=4.0.0
```

### Running the File System Watcher

```bash
cd watchers
pip install -r requirements.txt
python filesystem_watcher.py ../vault   # watches vault/Inbox/, writes to vault/Needs_Action/
```

Keep it alive with PM2:

```bash
pm2 start watchers/filesystem_watcher.py --interpreter python3 -- vault
pm2 save && pm2 startup
```

### vault-operator Skill

The `vault-operator` skill (`.claude/skills/vault-operator/SKILL.md`) teaches Claude how to:
- Read `Needs_Action`, create Plans, move items to `Done`
- Write approval requests to `Pending_Approval`
- Update `Dashboard.md` and append to `Logs/`

## MCP Tool Reference

The Playwright skill exposes 22 browser tools. See `.claude/skills/browsing-with-playwright/references/playwright-tools.md` for the full reference. Core tools: `browser_navigate`, `browser_snapshot`, `browser_take_screenshot`, `browser_click`, `browser_type`, `browser_fill_form`, `browser_evaluate`.

## Tech Stack

- **Claude Code**: Reasoning engine and orchestrator
- **Obsidian**: Local-first knowledge base and human GUI (vault = shared state)
- **MCP (Model Context Protocol)**: Interface to external actions
- **Playwright**: Browser automation (WhatsApp Web, payment portals, scraping)
- **Python 3.13+**: Watcher scripts and MCP client utilities
- **Node.js v24+ LTS**: MCP servers
