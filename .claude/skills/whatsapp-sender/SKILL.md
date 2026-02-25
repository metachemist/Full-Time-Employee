---
name: whatsapp-sender
description: |
  Send approved WhatsApp messages using the existing WhatsApp Web Playwright
  session. Use after a human has approved an APPROVAL_SEND_WHATSAPP_*.md file
  by moving it to vault/Approved/. Finds the contact in WhatsApp Web, sends
  the message, logs the result, and moves the approval file to vault/Done/.
  Never send without an approved file present.
---

# WhatsApp Sender

Execute WhatsApp message sends after human approval using WhatsApp Web via Playwright.

## When to Use

Only when:
1. An `APPROVAL_SEND_WHATSAPP_*.md` file exists in `vault/Approved/`
2. Its `status` field is `pending`

**Never message anyone not listed in the approved file's Target field.**

## Session Requirement

The WhatsApp Playwright session must exist at `$WHATSAPP_SESSION_PATH`
(default: `~/.sessions/whatsapp/`).

If the session is expired (QR code required again):
```bash
# Open WhatsApp watcher in headed mode to scan QR
WHATSAPP_HEADLESS=false python watchers/whatsapp_watcher.py ./vault
```

## Workflow

### 1. Read the Approved File

```bash
ls vault/Approved/APPROVAL_SEND_WHATSAPP_*.md
```

The approval file looks like:
```yaml
---
type: approval_request
action: send_whatsapp
source_plan: Plans/PLAN_WHATSAPP_...md
created: ...
status: pending
---

# Payload
- **Action:** `send_whatsapp`
- **Target:** Hafsa Shahid
- **Subject / Title:** N/A

## Message / Content
  Hi Hafsa! 👋
  Thanks for your message: "..."
  I'll get back to you soon.
```

Extract:
- **Contact name**: from `Target:` line (as it appears in WhatsApp chats)
- **Message**: everything under `## Message / Content`, strip the 2-space indent

### 2. Send the Message

```bash
python .claude/skills/whatsapp-sender/scripts/send_message.py \
  --to "Hafsa Shahid" \
  --message "Hi Hafsa! Thanks for reaching out..." \
  --session-path ~/.sessions/whatsapp
```

Or from a file for long messages:
```bash
python .claude/skills/whatsapp-sender/scripts/send_message.py \
  --to "Hafsa Shahid" \
  --message-file /tmp/whatsapp_body.txt \
  --session-path ~/.sessions/whatsapp
```

Expected output:
```json
{"status": "sent", "to": "Hafsa Shahid", "timestamp": "2026-02-22T..."}
```

### 3. Update the Vault

After successful send:
1. Set `status: sent` and `sent_at: <ISO>` in the approval file
2. Move approval file → `vault/Done/<filename>`
3. Write audit log to `vault/Logs/YYYY-MM-DD.jsonl`:
```json
{"timestamp": "...", "event": "whatsapp_sent", "to": "Hafsa Shahid", "result": "success"}
```
4. Update `Dashboard.md`

## How the Script Works

The `send_message.py` script automates these WhatsApp Web steps:

1. Launch Playwright with persistent context from `~/.sessions/whatsapp/`
2. Navigate to `https://web.whatsapp.com`
3. Wait for chat list to load (selector: `div[role="row"]`)
4. Search for the contact using the search box
5. Click the matching conversation row
6. Click the message input field and type the message
7. Press Enter to send
8. Wait for the sent tick (✓) to confirm delivery

## Dry-Run Mode

```bash
python .claude/skills/whatsapp-sender/scripts/send_message.py \
  --to "Test Contact" --message "Hello!" --dry-run
```

## Rules (non-negotiable)

- **Never** send without a file in `vault/Approved/`
- **Never** message a contact not named in the approval file
- **Never** send bulk/broadcast messages
- **Rate limit**: max 10 messages per hour
- **Always** log every send attempt (success or failure) to `vault/Logs/`
- **Always** move the approval file to `vault/Done/` after execution
- If the contact is **not found** in WhatsApp: write `status: failed_contact_not_found` to approval file, move to `vault/Done/`, alert in Dashboard
