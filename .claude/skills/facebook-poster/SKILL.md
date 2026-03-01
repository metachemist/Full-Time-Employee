---
name: facebook-poster
description: |
  Create and publish text posts to Facebook using a persistent Playwright
  session. Use when asked to post on Facebook, or when processing an approved
  APPROVAL_SEND_FACEBOOK_POST_*.md file from vault/Approved/.
  Always draft first and require human approval before posting.
---

# Facebook Poster

Publish posts to Facebook for business visibility and audience engagement.

## When to Use

- Processing an `APPROVAL_SEND_FACEBOOK_POST_*.md` file from `vault/Approved/`
- Creating a new post when explicitly instructed by the user
- Drafting post content for human review (no approval needed for drafts)

**Never post directly** — always route through `vault/Pending_Approval/` first.

## Session Setup (one-time)

Add to your `.env`:
```
FACEBOOK_SESSION_PATH=/home/you/.sessions/facebook
```

Then authenticate:
```bash
cd watchers && python auth_facebook.py
```

## Approval File Format

```markdown
---
type: approval_request
action: send_facebook_post
status: approved
---

# What will happen after approval?

The following post will be published to Facebook.

## Message / Content

  Your Facebook post text here.

  Can be multiple paragraphs. Facebook supports up to 63,206 characters.
```

Save to `vault/Approved/APPROVAL_SEND_FACEBOOK_POST_<topic>_<YYYY-MM-DD>.md`

## Dry-Run Mode

```bash
python .claude/skills/facebook-poster/scripts/create_post.py \
  --content "Post text..." --dry-run
```

## Rules

- **Never** post without a file in `vault/Approved/`
- **Always** screenshot the published post for the audit log
- **Always** log every post attempt to `vault/Logs/`
- **Max 3 posts per day** to avoid spam flags
