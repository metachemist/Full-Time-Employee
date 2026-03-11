# Personal AI Employee — Gold Tier

An autonomous agent that monitors Gmail, posts to social media, manages Odoo CRM, and executes approved actions. Built on Claude Code with Obsidian as the dashboard.

**Stack:** Claude Code · Obsidian · Gmail API · LinkedIn API · Facebook/Instagram Graph API · Twitter X API v2 · Odoo 19 JSON-RPC · Playwright · PM2

---

## How It Works

```
Gmail / LinkedIn / File drops
        ↓
   Watchers (Python, PM2)          ← always-on background daemons
        ↓
  vault/Needs_Action/              ← shared state (Obsidian vault)
        ↓
  Planning Engine (Claude Code)    ← reads, classifies, drafts
        ↓
  vault/Pending_Approval/          ← you review here
        ↓
  Move file to vault/Approved/     ← your only job
        ↓
  Approval Executor (30s poll)     ← dispatches to skill scripts
        ↓
  Email · LinkedIn · Twitter · Facebook · Instagram · Odoo
        ↓
  vault/Done/ + vault/Logs/        ← audit trail
```

Nothing is sent without you moving an approval file. Every action is logged.

---

## Setup

### 1. Install dependencies

```bash
git clone https://github.com/your-username/full_time_employee.git
cd full_time_employee

python3 -m venv .venv && source .venv/bin/activate
pip install -r watchers/requirements.txt
pip install pyyaml python-dotenv playwright playwright-stealth requests
playwright install chromium

npm install -g pm2
```

### 2. Configure `.env`

```bash
cp .env.example .env
# Fill in your credentials — see sections below
```

### 3. One-time auth per platform

| Platform | Command | Notes |
|---|---|---|
| Gmail | `python watchers/gmail_watcher.py vault` | Opens browser OAuth on first run — grant read + send scopes |
| LinkedIn | `python watchers/auth_linkedin_api.py` | Requires a LinkedIn Developer App with "Share on LinkedIn" product |
| Twitter/X | `python watchers/auth_twitter.py` | 3-step login: email → username → password. Do NOT use Google Sign-In |
| Facebook | Graph API Explorer → generate token with `pages_manage_posts` scope | Add `FACEBOOK_PAGE_ID` + `FACEBOOK_ACCESS_TOKEN` to `.env` |
| Instagram | Same Facebook app — add `instagram_content_publish` scope | `INSTAGRAM_USER_ID` must be the **numeric** Business Account ID |
| Odoo | Run Odoo locally (`docker run -p 8069:8069 odoo:latest`) | Add `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME` (email), `ODOO_PASSWORD` |

### 4. Start all processes

```bash
pm2 start ecosystem.config.js
pm2 save && pm2 startup
```

### 5. Open vault in Obsidian

Point Obsidian at the `vault/` folder. `Dashboard.md` shows live status. `Pending_Approval/` is your inbox.

---

## Daily Use

**Automatic:** Email arrives → watcher picks it up → planning engine drafts a reply → approval file appears in `vault/Pending_Approval/`.

**Your job:** Move the file to `vault/Approved/` to send, or `vault/Rejected/` to cancel. The executor acts within 30 seconds.

**Manual post (any platform):** Create a file directly in `vault/Approved/`:

```markdown
---
type: approval_request
action: send_email          # or: send_linkedin_post, send_twitter_post,
status: approved            #     send_facebook_post, send_instagram_post,
---                         #     odoo_create_lead, odoo_create_draft_invoice

- **Target:** recipient@example.com
- **Subject / Title:** Subject here

## Message / Content

  Your message here.
```

---

## Project Structure

```
watchers/          Perception layer — Gmail + filesystem daemons
orchestrator/      Planning engine — classifies items, creates plans + approvals
.claude/skills/    Action layer — one script per platform
vault/             Obsidian vault — shared state between all components
ecosystem.config.js PM2 process definitions
ARCHITECTURE.md    Full architecture + lessons learned
```

---

## Security

- `.env` is gitignored — never committed
- All outbound actions require a file in `vault/Approved/` — nothing sends autonomously
- Every action logged to `vault/Logs/YYYY-MM-DD.jsonl`
- Odoo invoices: draft only — never auto-posted

---

## License

MIT
