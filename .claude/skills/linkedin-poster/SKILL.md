---
name: linkedin-poster
description: |
  Create and publish LinkedIn posts for business/sales content using the
  existing LinkedIn Playwright session. Use when asked to post on LinkedIn,
  schedule a sales post, or process an approved LinkedIn post action from
  vault/Approved/. Always draft first and require human approval before posting.
---

# LinkedIn Poster

Publish posts to LinkedIn for business visibility and lead generation.

## When to Use

- Processing an `APPROVAL_SEND_LINKEDIN_POST_*.md` file from `vault/Approved/`
- Creating a new sales/business post when explicitly instructed by the user
- Drafting post content for human review (no approval needed for drafts)

**Never post directly** — always route through `vault/Pending_Approval/` first.

## Session Requirement

The LinkedIn Playwright session must already exist at `$LINKEDIN_SESSION_PATH`
(default: `~/.sessions/linkedin/`). If missing or expired, run:
```bash
cd watchers && python auth_linkedin.py
```

## Post Strategy — Sales Content Framework

Every LinkedIn post should follow one of these proven frameworks:

| Type | When to Use | Hook Pattern |
|------|-------------|--------------|
| **Problem → Solution** | New service/offer | "Most [audience] struggle with X. Here's how we solve it:" |
| **Story / Social Proof** | After a win or client result | "We helped [client type] achieve X in Y days. Here's exactly how:" |
| **Value List** | Education + awareness | "5 things [audience] should know about [topic]:" |
| **Direct Offer** | Promotion or launch | "We're opening [N] spots for [service]. Here's what's included:" |
| **Question / Poll** | Engagement | "Quick question for [audience]: [specific question]?" |

### Post Structure (always follow this)
```
[HOOK — 1-2 lines, no period at end, creates curiosity]

[BODY — 3-7 short paragraphs or bullet points, deliver value]

[CTA — 1 clear call to action: DM, comment, link in bio]

[HASHTAGS — 3-5 relevant hashtags on last line]
```

## Workflow

### 1. Draft the Post

Before posting, write the draft and save it:

```bash
# Save draft to a temp file for review
cat > /tmp/linkedin_post_draft.txt << 'EOF'
[Hook line here]

[Body paragraph 1]

[Body paragraph 2]

[CTA]

#hashtag1 #hashtag2 #hashtag3
EOF
```

Create an approval request in `vault/Pending_Approval/`:
```
vault/Pending_Approval/APPROVAL_SEND_LINKEDIN_POST_<topic>_<YYYY-MM-DD>.md
```

### 2. Post After Approval

Once the approval file is in `vault/Approved/`:

```bash
python .claude/skills/linkedin-poster/scripts/create_post.py \
  --content-file /tmp/linkedin_post_draft.txt \
  --session-path ~/.sessions/linkedin
```

Or inline for short posts:
```bash
python .claude/skills/linkedin-poster/scripts/create_post.py \
  --content "Your post text here..." \
  --session-path ~/.sessions/linkedin
```

Expected output:
```json
{"status": "posted", "post_url": "https://www.linkedin.com/feed/update/...", "timestamp": "..."}
```

### 3. Update the Vault

After successful post:
1. Set `status: posted` and add `posted_at: <ISO>` to the approval file
2. Move approval file → `vault/Done/`
3. Write audit log entry to `vault/Logs/YYYY-MM-DD.jsonl`:
```json
{"timestamp": "...", "event": "linkedin_post_published", "post_url": "...", "result": "success"}
```
4. Update `Dashboard.md`

## Playwright Steps (Manual Reference)

The `create_post.py` script automates these steps:

1. Launch with persistent context: `~/.sessions/linkedin/`
2. Navigate to `https://www.linkedin.com/feed/`
3. Click **"Start a post"** button
4. Wait for the post modal to open
5. Click into the text area and type the content
6. Click **"Post"** button
7. Wait for `networkidle` to confirm posting
8. Extract the post URL from the feed

## Content Calendar Template

When drafting a weekly content plan, use this structure:

```markdown
## LinkedIn Post Calendar — Week of YYYY-MM-DD

| Day | Type | Topic | Hook |
|-----|------|-------|------|
| Monday | Problem→Solution | [service] | "[hook]" |
| Wednesday | Value List | [topic] | "[hook]" |
| Friday | Story/Social Proof | [result] | "[hook]" |
```

## Dry-Run Mode

Preview what would be posted without opening a browser:
```bash
python .claude/skills/linkedin-poster/scripts/create_post.py \
  --content "Post text..." --dry-run
```

## Rules (non-negotiable)

- **Never** post without a file in `vault/Approved/`
- **Never** post competitor mentions, pricing without approval, or personal information
- **Max 1 post per day** — LinkedIn penalises over-posting
- **Always** screenshot the published post for the audit log
- **Always** log every post attempt to `vault/Logs/`
