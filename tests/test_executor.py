"""Tests for approval-executor/scripts/execute.py — field parsing, routing, rate limiting."""
import importlib
import sys
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# execute.py is imported via conftest.py sys.path manipulation
import execute


# ---------------------------------------------------------------------------
# _extract_field
# ---------------------------------------------------------------------------

class TestExtractField:
    def test_colon_inside_bold(self):
        body = "- **Target:** John Doe <john@example.com>"
        assert execute._extract_field(body, "Target") == "John Doe <john@example.com>"

    def test_colon_outside_bold(self):
        body = "- **Target**: Jane Smith"
        assert execute._extract_field(body, "Target") == "Jane Smith"

    def test_subject_title_field(self):
        body = "- **Subject / Title:** Re: Testing"
        assert execute._extract_field(body, "Subject / Title") == "Re: Testing"

    def test_missing_field_returns_empty(self):
        body = "- **Action:** send_email\n- **Target:** alice@example.com"
        assert execute._extract_field(body, "MissingField") == ""

    def test_action_field(self):
        body = "- **Action:** `send_email`"
        result = execute._extract_field(body, "Action")
        assert "send_email" in result


# ---------------------------------------------------------------------------
# _extract_message
# ---------------------------------------------------------------------------

class TestExtractMessage:
    def test_extracts_indented_content(self):
        body = "## Message / Content\n\n  Hello world\n  How are you?\n"
        msg = execute._extract_message(body)
        assert "Hello world" in msg
        assert "How are you?" in msg

    def test_strips_leading_whitespace(self):
        body = "## Message / Content\n\n  Line one\n  Line two\n"
        msg = execute._extract_message(body)
        # Should not start with 2-space indent after dedent
        assert not msg.startswith("  ")

    def test_returns_empty_when_no_section(self):
        body = "No message section here."
        assert execute._extract_message(body) == ""

    def test_stops_at_next_section(self):
        body = "## Message / Content\n\n  Hello\n\n## How to Approve\n\nOther stuff"
        msg = execute._extract_message(body)
        assert "Hello" in msg
        assert "Other stuff" not in msg


# ---------------------------------------------------------------------------
# _parse_approval
# ---------------------------------------------------------------------------

class TestParseApproval:
    def test_parses_frontmatter_and_body(self):
        text = "---\ntype: approval_request\naction: send_email\nstatus: pending\n---\n\nBody content."
        fm, body = execute._parse_approval(text)
        assert fm["action"] == "send_email"
        assert fm["status"] == "pending"
        assert "Body content." in body

    def test_no_frontmatter_returns_empty_dict(self):
        text = "Just plain text."
        fm, body = execute._parse_approval(text)
        assert fm == {}
        assert "Just plain text." in body

    def test_broken_yaml_returns_empty_dict(self):
        text = "---\n: bad: yaml:\n---\n\nBody."
        fm, body = execute._parse_approval(text)
        assert isinstance(fm, dict)


# ---------------------------------------------------------------------------
# _build_args
# ---------------------------------------------------------------------------

class TestBuildArgs:
    def test_send_email_valid(self):
        body = (
            "- **Target:** alice@example.com\n"
            "- **Subject / Title:** Hello Alice\n"
            "\n## Message / Content\n\n  Hi there, this is a test.\n"
        )
        args = execute._build_args("send_email", body)
        assert args is not None
        assert "--to" in args
        assert "alice@example.com" in args
        assert "--subject" in args
        assert "Hello Alice" in args
        assert "--body" in args

    def test_send_email_missing_target_returns_none(self):
        body = "## Message / Content\n\n  Some message content.\n"
        assert execute._build_args("send_email", body) is None

    def test_send_email_missing_message_returns_none(self):
        body = "- **Target:** bob@example.com\n- **Subject / Title:** Hi\n"
        assert execute._build_args("send_email", body) is None

    def test_send_linkedin_post_valid(self):
        body = "## Message / Content\n\n  Great post content here.\n"
        args = execute._build_args("send_linkedin_post", body)
        assert args is not None
        assert "--content" in args
        assert "Great post content here." in " ".join(args)

    def test_send_linkedin_post_missing_message_returns_none(self):
        assert execute._build_args("send_linkedin_post", "No message section.") is None

    def test_unknown_action_returns_none(self):
        assert execute._build_args("unknown_action", "body") is None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimit:
    def setup_method(self):
        # Reset rate state before each test
        execute._rate_state["bucket"] = None
        execute._rate_state["count"] = 0

    def test_allows_first_action(self):
        assert execute._rate_limit_check() is True

    def test_allows_up_to_max(self):
        for _ in range(execute._MAX_ACTIONS_PER_HOUR):
            assert execute._rate_limit_check() is True

    def test_blocks_after_max(self):
        for _ in range(execute._MAX_ACTIONS_PER_HOUR):
            execute._rate_limit_check()
        assert execute._rate_limit_check() is False

    def test_resets_on_new_hour(self):
        # Fill up the limit
        for _ in range(execute._MAX_ACTIONS_PER_HOUR):
            execute._rate_limit_check()
        # Simulate a new hour by manually changing the bucket
        execute._rate_state["bucket"] = "1970-01-01T00"
        assert execute._rate_limit_check() is True


# ---------------------------------------------------------------------------
# Expiry check (via execute_approval with a temp file)
# ---------------------------------------------------------------------------

class TestExpiryCheck:
    def test_expired_approval_is_skipped(self, tmp_path):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        content = (
            f"---\ntype: approval_request\naction: send_email\n"
            f"status: pending\nexpires_at: {past}\n---\n\n"
            "- **Target:** test@example.com\n"
            "- **Subject / Title:** Test\n\n"
            "## Message / Content\n\n  Hello.\n"
        )
        approval_file = tmp_path / "APPROVAL_test.md"
        approval_file.write_text(content)
        vault = tmp_path

        result = execute.execute_approval(approval_file, vault, dry_run=False)
        assert result["status"] == "skipped"
        assert "expired" in result["reason"].lower()

    def test_valid_approval_not_skipped_for_expiry(self, tmp_path):
        future = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        content = (
            f"---\ntype: approval_request\naction: unknown_action\n"
            f"status: pending\nexpires_at: {future}\n---\n\n"
            "Body.\n"
        )
        approval_file = tmp_path / "APPROVAL_test.md"
        approval_file.write_text(content)
        vault = tmp_path

        result = execute.execute_approval(approval_file, vault, dry_run=False)
        # Should NOT be skipped due to expiry — will fail for another reason (unknown action)
        assert result.get("reason", "") != f"Approval expired at {future}"
