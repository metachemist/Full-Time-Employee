# Skill: linkedin-dm

Send a direct message to a LinkedIn connection via their profile URL.

## When to use

Use this skill when:
- Processing an approved `APPROVAL_SEND_LINKEDIN_DM_*.md` file in `vault/Approved/`
- Replying to a LinkedIn DM that was detected by `watcher-linkedin`
- Sending a personalised outreach message to a lead

Always require human approval before sending. Never send cold messages autonomously.

## How it works

1. Opens the persistent LinkedIn Playwright session
2. Navigates to the recipient's LinkedIn profile URL
3. Clicks the **Message** button
4. Types the message into the chat input
5. Clicks **Send** (or presses Enter as fallback)
6. Screenshots the conversation for audit

## Approval file format

```markdown
---
type: approval_request
action: send_linkedin_dm
status: approved
---

# Payload

- **Target:** https://www.linkedin.com/in/their-username/
- **Subject / Title:** Re: your message

## Message / Content

  Hi [Name],

  Thanks for reaching out! Your message here.

  Best,
  Hafsa
```

The `Target` field must be the full LinkedIn profile URL.

## CLI usage

```bash
python .claude/skills/linkedin-dm/scripts/send_dm.py \
  --to-profile https://www.linkedin.com/in/username/ \
  --message "Hi, thanks for connecting!"

# Dry run (preview only)
python .claude/skills/linkedin-dm/scripts/send_dm.py \
  --to-profile https://www.linkedin.com/in/username/ \
  --message "Hi!" \
  --dry-run
```

## Output

```json
{
  "status": "sent",
  "to": "https://www.linkedin.com/in/username/",
  "screenshot": "/tmp/linkedin_dm_20260301_120000.png",
  "message_preview": "Hi, thanks for connecting!...",
  "timestamp": "2026-03-01T12:00:00+00:00"
}
```

## Notes

- Requires an active LinkedIn session at `LINKEDIN_SESSION_PATH`
- If session expired, run: `LINKEDIN_HEADLESS=false python watchers/auth_linkedin.py`
- Screenshot is always saved to `/tmp/linkedin_dm_<timestamp>.png` for audit
- Debug screenshot saved to `/tmp/linkedin_dm_debug.png` on selector failure
