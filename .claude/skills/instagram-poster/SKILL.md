---
name: instagram-poster
description: |
  Create and publish photo posts with captions to Instagram using a persistent
  Playwright session. Use when asked to post on Instagram, or when processing
  an approved APPROVAL_SEND_INSTAGRAM_POST_*.md file from vault/Approved/.
  Always draft first and require human approval before posting.
  NOTE: Instagram requires an image — text-only posts are not supported on web.
---

# Instagram Poster

Publish photo posts to Instagram for business visibility and brand building.

## When to Use

- Processing an `APPROVAL_SEND_INSTAGRAM_POST_*.md` file from `vault/Approved/`
- Creating a new post when explicitly instructed by the user

**Never post directly** — always route through `vault/Pending_Approval/` first.

## Important: Image Required

Instagram's web interface requires an image/video for every post.
The approval file must include an `Image:` field with a valid local file path.

Supported formats: JPG, PNG, MP4

## Session Setup (one-time)

Add to your `.env`:
```
INSTAGRAM_SESSION_PATH=/home/you/.sessions/instagram
```

Then authenticate:
```bash
cd watchers && python auth_instagram.py
```

## Approval File Format

```markdown
---
type: approval_request
action: send_instagram_post
status: approved
---

# What will happen after approval?

The following post will be published to Instagram.

# Payload

- **Image:** /path/to/image.jpg

## Message / Content

  Your Instagram caption here (max 2,200 chars).

  #hashtag1 #hashtag2 #hashtag3
```

Save to `vault/Approved/APPROVAL_SEND_INSTAGRAM_POST_<topic>_<YYYY-MM-DD>.md`

The executor reads the `Image:` field as the `target` and the `## Message / Content`
section as the caption.

## Caption Best Practices

- First line is the hook (shows before "more" cutoff)
- 3–10 hashtags on the last line
- Max 2,200 characters
- Emojis encouraged for engagement

## Dry-Run Mode

```bash
python .claude/skills/instagram-poster/scripts/create_post.py \
  --caption "Caption text..." \
  --image-path /path/to/image.jpg \
  --dry-run
```

## Rules

- **Never** post without a file in `vault/Approved/`
- **Image is mandatory** — the executor will error without a valid image path
- **Always** screenshot the published post for the audit log
- **Always** log every post attempt to `vault/Logs/`
- **Max 3 posts per day** to avoid spam flags
