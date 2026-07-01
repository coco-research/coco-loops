"""Tests for the loop verifier (A gate)."""

from __future__ import annotations

from pathlib import Path

import pytest

from coco_loops.verifier import (
    ChangeSpec,
    ExecutedTestResult,
    Verdict,
    check_constitution_violations,
    verify,
)


def _passing_runner(_change: ChangeSpec) -> ExecutedTestResult:
    return ExecutedTestResult(passed=True, output="ok", tests_run=3, tests_failed=0)


def _failing_runner(_change: ChangeSpec) -> ExecutedTestResult:
    return ExecutedTestResult(passed=False, output="FAILED test_x", tests_run=3, tests_failed=1)


class TestVerifierDefaultReject:
    def test_default_reject_without_test_runner_pass(self):
        change = ChangeSpec(description="trivial fix")
        result = verify(change, _failing_runner)
        assert result.verdict == Verdict.REJECT
        assert not result.approved
        assert any("REJECT" in e or "did not pass" in e for e in result.evidence)

    def test_does_not_trust_caller_claims_tests_passed(self):
        calls: list[ChangeSpec] = []

        def runner(change: ChangeSpec) -> ExecutedTestResult:
            calls.append(change)
            return ExecutedTestResult(passed=False, output="real failure")

        change = ChangeSpec(description="caller lied about green tests")
        result = verify(change, runner)
        assert len(calls) == 1
        assert result.verdict == Verdict.REJECT


class TestVerifierConstitution:
    def test_reject_constitution_edit_flag(self):
        change = ChangeSpec(proposes_constitution_edit=True)
        violation = check_constitution_violations(change)
        assert violation is not None
        assert violation.verdict == Verdict.REJECT

    def test_reject_constitution_path_touch(self):
        change = ChangeSpec(files_changed=["backend/app/loops/loop-constitution.md"])
        violation = check_constitution_violations(change)
        assert violation is not None
        assert violation.verdict == Verdict.REJECT

    def test_reject_verifier_disable(self):
        change = ChangeSpec(proposes_verifier_disable=True)
        violation = check_constitution_violations(change)
        assert violation is not None
        assert violation.verdict == Verdict.REJECT

    def test_reject_raw_cloud_egress(self):
        change = ChangeSpec(
            proposes_raw_cloud_egress=True,
            raw_cloud_hosts=["api.openai.com"],
        )
        violation = check_constitution_violations(change)
        assert violation is not None
        assert violation.verdict == Verdict.REJECT

    def test_escalate_denylisted_path(self):
        change = ChangeSpec(files_changed=["backend/alembic/versions/099_bad.py"])
        violation = check_constitution_violations(change)
        assert violation is not None
        assert violation.verdict == Verdict.ESCALATE_HUMAN

    def test_constitution_violation_skips_tests(self):
        runner_called = False

        def runner(_: ChangeSpec) -> ExecutedTestResult:
            nonlocal runner_called
            runner_called = True
            return ExecutedTestResult(passed=True)

        change = ChangeSpec(proposes_constraints_edit=True)
        result = verify(change, runner)
        assert result.verdict == Verdict.REJECT
        assert runner_called is False


class TestVerifierApprove:
    def test_approve_only_after_running_tests(self):
        calls: list[ChangeSpec] = []

        def runner(change: ChangeSpec) -> ExecutedTestResult:
            calls.append(change)
            return ExecutedTestResult(passed=True, tests_run=5, tests_failed=0)

        change = ChangeSpec(
            files_changed=["backend/app/foo.py"],
            description="safe fix",
        )
        result = verify(change, runner)
        assert len(calls) == 1
        assert result.verdict == Verdict.APPROVE
        assert result.approved
        assert any("executed tests" in e.lower() for e in result.evidence)

    def test_constitution_file_readable(self, monkeypatch: pytest.MonkeyPatch):
        constitution = Path(__file__).resolve().parents[1] / "specs" / "loop-constitution.md"
        assert constitution.exists()
        monkeypatch.setenv("COCO_LOOPS_DIR", str(constitution.parent))
