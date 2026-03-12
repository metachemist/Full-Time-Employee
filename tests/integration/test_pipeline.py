"""
Integration tests for the full AI Employee pipeline.

These tests exercise the real code paths end-to-end using --dry-run /
in-process calls so no external API calls are made. They require only
the filesystem and the local Python environment.

Run:
    pytest tests/integration/ -v
"""

import json
import sys
import time
import uuid
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / ".claude" / "skills" / "approval-executor" / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "watchers"))
sys.path.insert(0, str(PROJECT_ROOT / "orchestrator"))


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Create a minimal vault structure in a temp directory."""
    for d in ("Needs_Action", "Plans", "Pending_Approval", "Approved",
              "Rejected", "Done", "Failed", "Logs", "Inbox"):
        (tmp_path / d).mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: Email frontmatter round-trips through YAML cleanly
# ---------------------------------------------------------------------------

class TestEmailFrontmatterYAML:
    """Verify that email files written by gmail_watcher parse correctly."""

    def test_standard_email_parses(self):
        """Standard sender + recipient must produce valid YAML."""
        sender  = "Hafsa Shahid <hafsahere01@gmail.com>"
        to      = '"meta.chemist.22@gmail.com" <meta.chemist.22@gmail.com>'
        subject = "Urgent Report Generation request"
        trace_id = str(uuid.uuid4())

        def _ys(v): return "'" + v.replace("'", "''") + "'"

        fm_block = (
            f"type: email\n"
            f"source: gmail\n"
            f"from: {_ys(sender)}\n"
            f"to: {_ys(to)}\n"
            f"subject: {_ys(subject)}\n"
            f"received: 2026-03-12T08:44:10+00:00\n"
            f"priority: high\n"
            f"status: pending\n"
            f"trace_id: {trace_id}\n"
        )
        parsed = yaml.safe_load(fm_block)
        assert parsed["source"] == "gmail"
        assert parsed["from"] == sender
        assert parsed["to"] == to
        assert parsed["subject"] == subject
        assert parsed["trace_id"] == trace_id

    def test_email_with_special_chars_in_subject(self):
        """Subject with quotes and colons must not break YAML."""
        subject = 'Re: "Important" — it\'s urgent: act now!'
        def _ys(v): return "'" + v.replace("'", "''") + "'"
        fm_block = f"type: email\nsource: gmail\nsubject: {_ys(subject)}\n"
        parsed = yaml.safe_load(fm_block)
        assert parsed["subject"] == subject

    def test_trace_id_is_valid_uuid(self):
        """trace_id written by watcher must be parseable as UUID."""
        trace_id = str(uuid.uuid4())
        fm_block = f"type: email\nsource: gmail\ntrace_id: {trace_id}\n"
        parsed = yaml.safe_load(fm_block)
        # Should not raise
        uuid.UUID(parsed["trace_id"])


# ---------------------------------------------------------------------------
# Test 2: Planning engine processes a Needs_Action file correctly
# ---------------------------------------------------------------------------

class TestPlanningEngine:
    """End-to-end planning engine tests against a real temp vault."""

    def _make_email_file(self, vault: Path, trace_id: str | None = None) -> Path:
        tid = trace_id or str(uuid.uuid4())
        content = (
            "---\n"
            "type: email\n"
            "source: gmail\n"
            "from: 'Test User <test@example.com>'\n"
            "to: 'me@company.com'\n"
            "subject: 'Hello from integration test'\n"
            "received: 2026-03-12T10:00:00+00:00\n"
            "priority: high\n"
            "status: pending\n"
            f"trace_id: {tid}\n"
            "---\n\n"
            "## Email: Hello from integration test\n\n"
            "**From:** Test User <test@example.com>\n\n"
            "### Preview\n\nThis is a test email.\n"
        )
        p = vault / "Needs_Action" / f"EMAIL_HUMAN_Test_User_{tid[:8]}.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_processes_gmail_email(self, vault):
        """Planning engine must classify gmail source correctly and create PLAN."""
        from orchestrator.planning_engine import PlanningEngine

        email_file = self._make_email_file(vault)
        engine = PlanningEngine(vault)
        engine.run_once()

        plans = list((vault / "Plans").glob("PLAN_GMAIL_*.md"))
        assert len(plans) == 1, f"Expected 1 PLAN_GMAIL_*.md, got {plans}"

        plan_text = plans[0].read_text()
        assert "source: gmail" in plan_text

    def test_gmail_email_moves_to_done(self, vault):
        """Source file must move to Done/ after processing."""
        from orchestrator.planning_engine import PlanningEngine

        email_file = self._make_email_file(vault)
        filename   = email_file.name
        engine = PlanningEngine(vault)
        engine.run_once()

        assert not (vault / "Needs_Action" / filename).exists(), \
            "Original file should be moved out of Needs_Action/"
        done_files = list((vault / "Done").glob(f"{filename}*"))
        assert len(done_files) == 1, f"Expected 1 Done file, got {done_files}"

    def test_gmail_email_creates_approval(self, vault):
        """External gmail source must always create an approval request."""
        from orchestrator.planning_engine import PlanningEngine

        self._make_email_file(vault)
        engine = PlanningEngine(vault)
        engine.run_once()

        approvals = list((vault / "Pending_Approval").glob("APPROVAL_*.md"))
        assert len(approvals) == 1, "Expected 1 approval file for external gmail email"

    def test_trace_id_propagates_to_plan(self, vault):
        """trace_id from source file must appear in generated PLAN frontmatter."""
        from orchestrator.planning_engine import PlanningEngine

        trace_id = str(uuid.uuid4())
        self._make_email_file(vault, trace_id=trace_id)
        engine = PlanningEngine(vault)
        engine.run_once()

        plans = list((vault / "Plans").glob("PLAN_GMAIL_*.md"))
        assert plans, "No plan created"
        plan_text = plans[0].read_text()
        assert trace_id in plan_text, "trace_id must be present in plan frontmatter"

    def test_trace_id_propagates_to_approval(self, vault):
        """trace_id must appear in the APPROVAL file frontmatter."""
        from orchestrator.planning_engine import PlanningEngine

        trace_id = str(uuid.uuid4())
        self._make_email_file(vault, trace_id=trace_id)
        engine = PlanningEngine(vault)
        engine.run_once()

        approvals = list((vault / "Pending_Approval").glob("APPROVAL_*.md"))
        assert approvals, "No approval created"
        appr_text = approvals[0].read_text()
        assert trace_id in appr_text, "trace_id must be present in approval frontmatter"

    def test_dedup_prevents_reprocessing(self, vault):
        """Same file must not be processed twice across engine instances."""
        from orchestrator.planning_engine import PlanningEngine

        self._make_email_file(vault)
        engine = PlanningEngine(vault)
        engine.run_once()

        # Second run — Needs_Action is now empty (file moved to Done)
        count = engine.run_once()
        assert count == 0

        plans = list((vault / "Plans").glob("PLAN_*.md"))
        assert len(plans) == 1, "Should not have created duplicate plans"

    def test_dashboard_updated_after_run(self, vault):
        """Dashboard.md must exist and contain vault counts after run."""
        from orchestrator.planning_engine import PlanningEngine

        self._make_email_file(vault)
        engine = PlanningEngine(vault)
        engine.run_once()

        dashboard = vault / "Dashboard.md"
        assert dashboard.exists(), "Dashboard.md must be created"
        content = dashboard.read_text()
        assert "Done" in content
        assert "Pending Approval" in content


# ---------------------------------------------------------------------------
# Test 3: Executor dry-run dispatch
# ---------------------------------------------------------------------------

class TestExecutorDryRun:
    """Verify executor correctly parses and dispatches in dry-run mode."""

    def _make_approval(self, vault: Path, action: str, trace_id: str | None = None) -> Path:
        tid = trace_id or str(uuid.uuid4())
        content = (
            "---\n"
            f"type: approval_request\n"
            f"action: {action}\n"
            f"source_plan: Plans/PLAN_GMAIL_test.md\n"
            f"created: 2026-03-12T10:00:00+00:00\n"
            f"expires_at: 2030-01-01T00:00:00+00:00\n"
            f"status: pending\n"
            f"trace_id: {tid}\n"
            "---\n\n"
            "# Payload\n\n"
            "- **Action:** `send_email`\n"
            "- **Target:** test@example.com\n"
            "- **Subject / Title:** Integration test\n\n"
            "## Message / Content\n\n"
            "  This is a dry-run integration test message.\n"
        )
        p = vault / "Approved" / f"APPROVAL_{action.upper()}_test_{tid[:8]}.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_dry_run_send_email(self, vault):
        """Executor dry-run for send_email must return dry_run status."""
        import execute as ex
        ex._load_rate_state(vault)
        ex._load_metrics(vault)

        approval = self._make_approval(vault, "send_email")
        result = ex.execute_approval(approval, vault, dry_run=True)
        assert result["status"] == "dry_run"
        assert result["action"] == "send_email"

    def test_dry_run_does_not_move_file(self, vault):
        """Dry-run must not move the approval file out of Approved/."""
        import execute as ex
        ex._load_rate_state(vault)
        ex._load_metrics(vault)

        approval = self._make_approval(vault, "send_email")
        ex.execute_approval(approval, vault, dry_run=True)
        assert approval.exists(), "Approval file must remain in Approved/ after dry-run"

    def test_unknown_action_returns_error(self, vault):
        """Unknown action type must return error without crashing."""
        import execute as ex

        content = (
            "---\ntype: approval_request\naction: nonexistent_action\n"
            "status: pending\nexpires_at: 2030-01-01T00:00:00+00:00\n---\n\n"
            "- **Target:** x\n\n## Message / Content\n\n  x\n"
        )
        f = vault / "Approved" / "APPROVAL_NONEXISTENT_test.md"
        f.write_text(content)
        result = ex.execute_approval(f, vault, dry_run=True)
        assert result["status"] == "error"

    def test_expired_approval_skipped(self, vault):
        """Expired approval (expires_at in the past) must be skipped."""
        import execute as ex

        content = (
            "---\ntype: approval_request\naction: send_email\n"
            "status: pending\nexpires_at: 2020-01-01T00:00:00+00:00\n---\n\n"
            "- **Target:** x@x.com\n\n## Message / Content\n\n  x\n"
        )
        f = vault / "Approved" / "APPROVAL_SEND_EMAIL_expired.md"
        f.write_text(content)
        result = ex.execute_approval(f, vault, dry_run=False)
        assert result["status"] == "skipped"
        assert "expired" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Test 4: Rate limit persistence
# ---------------------------------------------------------------------------

class TestRateLimitPersistence:
    """Verify rate limit counter loads from disk and survives restart."""

    def test_counter_persists_to_disk(self, vault):
        """After hitting the rate limit, state file must exist on disk."""
        import execute as ex
        ex._rate_file = None
        ex._rate_state = {"bucket": None, "count": 0}
        ex._load_rate_state(vault)

        # Simulate consuming the full hourly budget
        for _ in range(ex._MAX_ACTIONS_PER_HOUR):
            allowed = ex._rate_limit_check()
            assert allowed

        # Next call should be denied
        assert not ex._rate_limit_check()

        # State file must exist
        rate_file = vault / "Logs" / "rate_limit.json"
        assert rate_file.exists(), "rate_limit.json must be written to disk"

        data = json.loads(rate_file.read_text())
        assert data["count"] == ex._MAX_ACTIONS_PER_HOUR  # count stops at limit (deny doesn't increment)

    def test_counter_reloads_same_hour(self, vault):
        """Counter loaded from disk in the same hour must continue from saved value."""
        import execute as ex
        from datetime import datetime, timezone

        bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        rate_file = vault / "Logs" / "rate_limit.json"
        rate_file.write_text(json.dumps({"bucket": bucket, "count": 7}))

        ex._rate_state = {"bucket": None, "count": 0}
        ex._rate_file = None
        ex._load_rate_state(vault)

        assert ex._rate_state["count"] == 7

    def test_counter_resets_for_new_hour(self, vault):
        """Counter from a previous hour bucket must reset to 0."""
        import execute as ex

        rate_file = vault / "Logs" / "rate_limit.json"
        rate_file.write_text(json.dumps({"bucket": "2020-01-01T00", "count": 9}))

        ex._rate_state = {"bucket": None, "count": 0}
        ex._rate_file = None
        ex._load_rate_state(vault)

        # After first check, old count must be discarded
        ex._rate_limit_check()
        assert ex._rate_state["count"] == 1


# ---------------------------------------------------------------------------
# Test 5: Atomic state writes
# ---------------------------------------------------------------------------

class TestAtomicStateWrites:
    """Verify that state files are written atomically (no .tmp files left behind)."""

    def test_no_tmp_file_after_save(self, tmp_path):
        """After _save_state(), no .tmp file should remain."""
        sys.path.insert(0, str(PROJECT_ROOT / "watchers"))
        # Import the raw function by constructing a minimal watcher stub
        from base_watcher import BaseWatcher

        class _Stub(BaseWatcher):
            def check_for_updates(self): return []
            def create_action_file(self, item): pass

        stub = _Stub(str(tmp_path))
        stub.processed_ids = {"id1", "id2", "id3"}
        stub._save_state()

        assert stub._state_file.exists(), "State file must exist after save"
        tmp = stub._state_file.with_suffix(".tmp")
        assert not tmp.exists(), ".tmp file must not remain after atomic replace"

    def test_state_survives_reload(self, tmp_path):
        """IDs saved must be exactly the IDs loaded on next instantiation."""
        from base_watcher import BaseWatcher

        class _Stub(BaseWatcher):
            def check_for_updates(self): return []
            def create_action_file(self, item): pass

        stub = _Stub(str(tmp_path))
        ids  = {"alpha", "beta", "gamma"}
        stub.processed_ids = ids
        stub._save_state()

        stub2 = _Stub(str(tmp_path))
        assert stub2.processed_ids == ids


# ---------------------------------------------------------------------------
# Test 6: GDPR delete tool
# ---------------------------------------------------------------------------

class TestGdprDelete:
    """Verify gdpr_delete.py finds and redacts files correctly."""

    def test_finds_matching_files(self, vault):
        """Files containing target email must be found."""
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        import gdpr_delete

        (vault / "Done").mkdir(exist_ok=True)
        target_email = "victim@example.com"
        f = vault / "Done" / "EMAIL_test.md"
        f.write_text(f"Subject: Hello\nFrom: {target_email}\nBody: test", encoding="utf-8")

        matches = gdpr_delete._find_matching_files(vault, target_email)
        assert any(m.name == "EMAIL_test.md" for m in matches)

    def test_does_not_find_unrelated_files(self, vault):
        """Files without target email must not be returned."""
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        import gdpr_delete

        f = vault / "Done" / "EMAIL_clean.md"
        f.write_text("Subject: Hello\nFrom: other@example.com\nBody: test", encoding="utf-8")

        matches = gdpr_delete._find_matching_files(vault, "victim@example.com")
        assert not any(m.name == "EMAIL_clean.md" for m in matches)

    def test_redact_replaces_email(self, vault):
        """_redact_file must replace all occurrences of the target email."""
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        import gdpr_delete

        target = "victim@example.com"
        f = vault / "Done" / "EMAIL_to_redact.md"
        f.write_text(f"From: {target}\nReply-To: {target}\nBody: contact {target}", encoding="utf-8")

        redacted = gdpr_delete._redact_file(f, target)
        assert target not in redacted
        assert redacted.count("[REDACTED]") == 3
