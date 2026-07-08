"""C2 (supervisor watch driver) + C3 (run-log labeling -> promotion-gate exercise)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from coco_loops.cli import main
from coco_loops.paths import reset_config
from coco_loops.promotion_gate import PromotionGateConfig, evaluate_gate
from coco_loops.run_log import RunLog, RunLogEntry
from coco_loops.supervisor import (
    SupervisorAction,
    SupervisorConfig,
    WatchConfig,
    watch,
)

FIXED = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
LOOP = "sup-loop"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    run_log = state / "run-log.jsonl"
    monkeypatch.setenv("COCO_LOOPS_HOME", str(state))
    monkeypatch.setenv("COCO_LOOP_RUN_LOG", str(run_log))
    reset_config()
    yield {"state": state, "run_log": run_log}
    reset_config()


def _entry(run_id: str, **kw) -> RunLogEntry:
    base = dict(run_id=run_id, items_found=1, outcome="fix-proposed", last_progress="p")
    base.update(kw)
    return RunLogEntry(**base)


class TestSupervisorWatch:
    def test_alive_is_none_action(self, env):
        log = RunLog(env["run_log"])
        log.append(_entry("seed", last_progress=FIXED.isoformat()))
        cfg = WatchConfig(
            loop_id=LOOP,
            supervisor=SupervisorConfig(cadence_seconds=900, tolerance_intervals=3),
            process_checker=lambda: True,
            once=True,
        )
        decisions = watch(cfg, log, clock=lambda: FIXED)
        assert len(decisions) == 1
        assert decisions[0].action == SupervisorAction.NONE

    def test_dead_process_revives_and_logs(self, env):
        log = RunLog(env["run_log"])
        revived = {"n": 0}
        cfg = WatchConfig(
            loop_id=LOOP,
            supervisor=SupervisorConfig(cadence_seconds=900, max_revives=3),
            process_checker=lambda: False,  # DEAD
            revive_fn=lambda: revived.__setitem__("n", revived["n"] + 1),
            once=True,
        )
        decisions = watch(cfg, log, clock=lambda: FIXED)
        assert decisions[0].action == SupervisorAction.REVIVE
        assert revived["n"] == 1
        entries = log.read()
        assert any(e.outcome == "supervisor-revive" and e.loop_id == LOOP for e in entries)

    def test_escalates_after_revive_budget_exhausted(self, env):
        log = RunLog(env["run_log"])
        revived = {"n": 0}
        cfg = WatchConfig(
            loop_id=LOOP,
            supervisor=SupervisorConfig(cadence_seconds=900, max_revives=2),
            process_checker=lambda: False,
            revive_fn=lambda: revived.__setitem__("n", revived["n"] + 1),
            once=False,
            max_iterations=5,
        )
        decisions = watch(cfg, log, clock=lambda: FIXED, sleep_fn=lambda _s: None)
        assert [d.action for d in decisions] == [
            SupervisorAction.REVIVE,
            SupervisorAction.REVIVE,
            SupervisorAction.ESCALATE_HUMAN,
        ]
        assert revived["n"] == 2
        assert any(e.escalations for e in log.read())

    def test_cli_supervise_smoke(self, env, capsys):
        rc = main(["supervise", LOOP])
        out = capsys.readouterr().out
        assert rc == 0
        assert "liveness=" in out


class TestRunLogVerdict:
    def test_set_verdict_updates_matching_entry(self, tmp_path):
        log = RunLog(tmp_path / "rl.jsonl")
        log.append(_entry("a"))
        log.append(_entry("b"))
        assert log.set_verdict("a", "correct") is True
        by_id = {e.run_id: e for e in log.read()}
        assert by_id["a"].human_verdict == "correct"
        assert by_id["b"].human_verdict is None

    def test_set_verdict_missing_returns_false(self, tmp_path):
        log = RunLog(tmp_path / "rl.jsonl")
        log.append(_entry("a"))
        assert log.set_verdict("missing", "x") is False

    def test_cli_label(self, env, capsys):
        log = RunLog(env["run_log"])
        log.append(_entry("r1"))
        rc = main(["label", "r1", "false_positive"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "labeled r1" in out
        assert {e.run_id: e for e in log.read()}["r1"].human_verdict == "false_positive"


class TestPromotionGateExercise:
    """Exercise the L1->L2 gate over a realistically labeled run-log."""

    def _gate_inputs(self, tmp_path: Path):
        budget = tmp_path / "loop-budget.md"
        budget.write_text("# budget\n", encoding="utf-8")
        proven = tmp_path / "verifier-proven.json"
        proven.write_text('{"verifier_proven": true}', encoding="utf-8")
        return budget, proven

    def _labeled_log(self, path: Path, fp_count: int, total: int) -> RunLog:
        log = RunLog(path)
        for i in range(total):
            log.append(_entry(f"r{i}", loop_id=LOOP))
        for i in range(total):
            log.set_verdict(f"r{i}", "false_positive" if i < fp_count else "correct")
        return log

    def test_fp_rate_at_ceiling_fails(self, tmp_path):
        budget, proven = self._gate_inputs(tmp_path)
        run_log = tmp_path / "rl.jsonl"
        self._labeled_log(run_log, fp_count=1, total=5)  # 20% == 20% ceiling
        verdict = evaluate_gate(
            PromotionGateConfig(
                budget_artifact=budget,
                run_log_path=run_log,
                verifier_proven_flag_path=proven,
                comprehension_signed_off=True,
            )
        )
        assert verdict.false_positive_rate == pytest.approx(0.2)
        assert verdict.labeled_entries == 5
        assert verdict.passed is False
        assert any("False-positive rate" in r for r in verdict.reasons)

    def test_clean_labels_pass(self, tmp_path):
        budget, proven = self._gate_inputs(tmp_path)
        run_log = tmp_path / "rl.jsonl"
        self._labeled_log(run_log, fp_count=0, total=5)  # 0% FP
        verdict = evaluate_gate(
            PromotionGateConfig(
                budget_artifact=budget,
                run_log_path=run_log,
                verifier_proven_flag_path=proven,
                comprehension_signed_off=True,
            )
        )
        assert verdict.false_positive_rate == pytest.approx(0.0)
        assert verdict.passed is True
