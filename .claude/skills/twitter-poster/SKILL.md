---
name: twitter-poster
description: |
  Create and publish posts (tweets) to Twitter/X using a persistent Playwright
  session. Use when asked to post on X/Twitter, or when processing an approved
  APPROVAL_SEND_TWITTER_POST_*.md file from vault/Approved/.
  Always draft first and require human approval before posting.
---

# Twitter/X Poster

Publish posts to Twitter/X for business visibility and audience growth.

## When to Use

- Processing an `APPROVAL_SEND_TWITTER_POST_*.md` file from `vault/Approved/`
- Creating a new tweet when explicitly instructed by the user
- Drafting tweet content for human review (no approval needed for drafts)

**Never post directly** — always route through `vault/Pending_Approval/` first.

## Session Setup (one-time)

Add to your `.env`:
```
TWITTER_SESSION_PATH=/home/you/.sessions/twitter
```

Then authenticate:
```bash
cd watchers && python auth_twitter.py
```

Log in with your X email + password (NOT "Continue with Google" — Google OAuth
is blocked by Playwright's automation detection).

## Character Limit

Standard X accounts: **280 characters** (including spaces and line breaks).

## Approval File Format

```markdown
---
type: approval_request
action: send_twitter_post
status: approved
---

# What will happen after approval?

The following tweet will be posted to X/Twitter.

## Message / Content

  Your tweet text here (max 280 chars).

  #hashtag1 #hashtag2
```

Save to `vault/Approved/APPROVAL_SEND_TWITTER_POST_<topic>_<YYYY-MM-DD>.md`

## Tweet Strategy

| Format | When to Use | Pattern |
|--------|-------------|---------|
| **Hook + Value** | New content/offer | "Most people don't know X. Here's why:" |
| **Thread opener** | Deep dive | "A thread on [topic] 🧵" |
| **Question** | Engagement | "What's your take on [topic]?" |
| **Social proof** | After a win | "Just helped a client [result]. Here's what worked:" |
| **Hot take** | Opinion/debate | "[Controversial but true statement]." |

### Tweet Structure
```
[Hook — punchy opener, creates curiosity or delivers value]

[1-3 short lines of body — no filler words]

[CTA or hashtags]
```

## Workflow

### 1. Draft the Tweet

Write the draft (max 280 chars) and create an approval request:

```
vault/Pending_Approval/APPROVAL_SEND_TWITTER_POST_<topic>_<YYYY-MM-DD>.md
```

### 2. Post After Approval

Once the approval file is moved to `vault/Approved/`, the executor runs:

```bash
python .claude/skills/twitter-poster/scripts/create_post.py \
  --content "Your tweet text..." \
  --session-path ~/.sessions/twitter
```

Expected output:
```json
{"status": "posted", "screenshot": "/tmp/twitter_post_20260301_080000.png", "timestamp": "..."}
```

### 3. Update the Vault

After successful post:
1. Set `status: posted` in the approval file
2. Move approval file → `vault/Done/`
3. Write audit entry to `vault/Logs/YYYY-MM-DD.jsonl`
4. Update `Dashboard.md`

## Dry-Run Mode

Preview without opening a browser:
```bash
python .claude/skills/twitter-poster/scripts/create_post.py \
  --content "Tweet text..." --dry-run
```

## Rules (non-negotiable)

- **Never** post without a file in `vault/Approved/`
- **Max 280 characters** — executor rejects longer content
- **Max 3-5 posts per day** — avoid spam flags
- **Always** screenshot the published post for the audit log
- **Never** post competitor mentions, personal information, or pricing without explicit approval
- **Always** log every post attempt to `vault/Logs/`
