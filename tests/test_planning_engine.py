"""Tests for orchestrator/planning_engine.py — classification, parsing, helpers."""
import pytest
from orchestrator.planning_engine import (
    classify_risk,
    needs_approval,
    parse_md,
    generate_draft,
    _safe_id,
    _sender,
    _subject,
)


# ---------------------------------------------------------------------------
# classify_risk
# ---------------------------------------------------------------------------

class TestClassifyRisk:
    def test_high_risk_lawsuit(self):
        assert classify_risk("there is a lawsuit pending against you") == "high"

    def test_high_risk_fraud(self):
        assert classify_risk("suspected fraud detected on your account") == "high"

    def test_high_risk_hack(self):
        # "hack" keyword matched by word tokenizer (\w+)
        assert classify_risk("evidence of a hack on the server") == "high"

    def test_medium_risk_pricing(self):
        assert classify_risk("can you send me a pricing quote?") == "medium"

    def test_medium_risk_contract(self):
        assert classify_risk("I need to review the contract terms") == "medium"

    def test_medium_risk_invoice(self):
        assert classify_risk("please send me an invoice for the work") == "medium"

    def test_low_risk_greeting(self):
        assert classify_risk("just saying hello, hope you are well") == "low"

    def test_low_risk_file_drop(self):
        assert classify_risk("a new file has been dropped into inbox") == "low"

    def test_high_overrides_medium(self):
        # Text with both high and medium keywords → high
        assert classify_risk("urgent legal dispute about invoice") == "high"


# ---------------------------------------------------------------------------
# needs_approval
# ---------------------------------------------------------------------------

class TestNeedsApproval:
    def test_external_gmail_always_approved(self):
        assert needs_approval("routine update", "gmail", "low") is True

    def test_external_linkedin_always_approved(self):
        assert needs_approval("hey there", "linkedin", "low") is True

    def test_high_risk_internal_approved(self):
        assert needs_approval("lawsuit content", "file_drop", "high") is True

    def test_trigger_word_send_approved(self):
        assert needs_approval("please send a reply", "file_drop", "low") is True

    def test_trigger_word_payment_approved(self):
        assert needs_approval("payment received", "file_drop", "low") is True

    def test_internal_low_no_triggers(self):
        assert needs_approval("routine file uploaded", "file_drop", "low") is False

    def test_medium_risk_internal_no_triggers(self):
        # "pricing" is also in _APPROVAL_TRIGGERS so use "retainer" which is only in medium_risk
        assert needs_approval("retainer agreement discussion", "file_drop", "medium") is False


# ---------------------------------------------------------------------------
# parse_md
# ---------------------------------------------------------------------------

class TestParseMd:
    def test_with_frontmatter(self):
        text = "---\ntype: email\nfrom: Alice <alice@example.com>\n---\n\nBody text here."
        fm, body = parse_md(text)
        assert fm["type"] == "email"
        assert fm["from"] == "Alice <alice@example.com>"
        assert "Body text here." in body

    def test_without_frontmatter(self):
        text = "Just plain body text."
        fm, body = parse_md(text)
        assert fm == {}
        assert body == "Just plain body text."

    def test_broken_frontmatter_returns_empty_dict(self):
        text = "---\n: broken: yaml:\n---\n\nBody."
        fm, body = parse_md(text)
        assert isinstance(fm, dict)

    def test_empty_frontmatter(self):
        text = "---\n---\n\nJust body."
        fm, body = parse_md(text)
        assert fm == {}
        assert "Just body." in body


# ---------------------------------------------------------------------------
# _safe_id
# ---------------------------------------------------------------------------

class TestSafeId:
    def test_max_length(self):
        assert len(_safe_id("a" * 100, 36)) <= 36

    def test_replaces_special_chars(self):
        result = _safe_id("hello world! foo@bar.com")
        assert " " not in result
        assert "@" not in result
        assert "." not in result

    def test_preserves_alphanumeric(self):
        result = _safe_id("HelloWorld123")
        assert result == "HelloWorld123"

    def test_default_max_len(self):
        assert len(_safe_id("x" * 200)) <= 36


# ---------------------------------------------------------------------------
# _sender
# ---------------------------------------------------------------------------

class TestSender:
    def test_strips_email_angle_bracket(self):
        fm = {"from": "John Doe <john@example.com>"}
        assert _sender(fm) == "John Doe"

    def test_plain_name(self):
        fm = {"from": "Jane Smith"}
        assert _sender(fm) == "Jane Smith"

    def test_falls_back_to_sender_key(self):
        fm = {"sender": "Bob"}
        assert _sender(fm) == "Bob"

    def test_falls_back_to_name_key(self):
        fm = {"name": "Alice"}
        assert _sender(fm) == "Alice"

    def test_unknown_when_no_keys(self):
        assert _sender({}) == "Unknown"


# ---------------------------------------------------------------------------
# _subject
# ---------------------------------------------------------------------------

class TestSubject:
    def test_subject_key(self):
        fm = {"subject": "Re: Proposal"}
        assert _subject(fm) == "Re: Proposal"

    def test_falls_back_to_topic(self):
        fm = {"topic": "Partnership discussion"}
        assert _subject(fm) == "Partnership discussion"

    def test_falls_back_to_kind(self):
        fm = {"kind": "connection_request"}
        assert _subject(fm) == "Connection Request"

    def test_na_when_empty(self):
        assert _subject({}) == "N/A"


# ---------------------------------------------------------------------------
# generate_draft
# ---------------------------------------------------------------------------

class TestGenerateDraft:
    def test_email_draft_contains_subject(self):
        fm = {"from": "Alice <alice@example.com>", "subject": "Test Subject"}
        draft = generate_draft("gmail", fm, "Some email body.")
        assert "Test Subject" in draft
        assert "Alice" in draft

    def test_linkedin_dm_draft_contains_name(self):
        fm = {"from": "Bob Builder", "kind": "dm"}
        draft = generate_draft("linkedin", fm, "Hello there!")
        assert "Bob" in draft

    def test_linkedin_connection_draft(self):
        fm = {"from": "Carol", "kind": "connection_request"}
        draft = generate_draft("linkedin", fm, "")
        assert "Carol" in draft
        assert "connecting" in draft.lower()

    def test_file_drop_draft(self):
        fm = {"original_name": "report.pdf", "size_bytes": 1024}
        draft = generate_draft("file_drop", fm, "")
        assert "report.pdf" in draft
        assert "1024" in draft

    def test_unknown_source_uses_file_draft(self):
        # Unknown source falls back to _draft_file
        draft = generate_draft("unknown_source", {}, "")
        assert isinstance(draft, str)
        assert len(draft) > 0
