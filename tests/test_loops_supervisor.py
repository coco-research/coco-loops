"""Tests for loop heartbeat supervisor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from coco_loops.run_log import RunLog, RunLogEntry
from coco_loops.supervisor import (
    Liveness,
    SupervisorAction,
    SupervisorConfig,
    SupervisorState,
    apply_revive,
    classify_liveness,
    decide,
)


def _fixed_clock(fixed: datetime):
    def _clock() -> datetime:
        return fixed

    return _clock


class TestClassifyLiveness:
    def test_dead_when_process_gone(self):
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        assert (
            classify_liveness(
                last_progress=now.isoformat(),
                now=now,
                cadence_seconds=300,
                tolerance_intervals=3,
                process_alive=False,
            )
            == Liveness.DEAD
        )

    def test_alive_with_recent_progress(self):
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        progress = (now - timedelta(seconds=100)).isoformat()
        assert (
            classify_liveness(
                last_progress=progress,
                now=now,
                cadence_seconds=300,
                tolerance_intervals=3,
                process_alive=True,
            )
            == Liveness.ALIVE
        )

    def test_dozing_when_stale_progress(self):
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        progress = (now - timedelta(seconds=1000)).isoformat()
        assert (
            classify_liveness(
                last_progress=progress,
                now=now,
                cadence_seconds=300,
                tolerance_intervals=3,
                process_alive=True,
            )
            == Liveness.DOZING
        )


class TestSupervisorDecide:
    def _log_with_progress(self, tmp_path: Path, progress: str) -> RunLog:
        log = RunLog(tmp_path / "run-log.jsonl")
        log.append(
            RunLogEntry(
                run_id="r1",
                items_found=0,
                last_progress=progress,
            )
        )
        return log

    def test_alive_no_action(self, tmp_path: Path):
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        log = self._log_with_progress(tmp_path, (now - timedelta(seconds=60)).isoformat())
        decision = decide(
            config=SupervisorConfig(cadence_seconds=300),
            state=SupervisorState(),
            run_log=log,
            process_checker=lambda: True,
            clock=_fixed_clock(now),
        )
        assert decision.liveness == Liveness.ALIVE
        assert decision.action == SupervisorAction.NONE

    def test_dead_revive(self, tmp_path: Path):
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        log = self._log_with_progress(tmp_path, now.isoformat())
        decision = decide(
            config=SupervisorConfig(cadence_seconds=300, max_revives=2),
            state=SupervisorState(),
            run_log=log,
            process_checker=lambda: False,
            clock=_fixed_clock(now),
        )
        assert decision.liveness == Liveness.DEAD
        assert decision.action == SupervisorAction.REVIVE

    def test_dozing_revive(self, tmp_path: Path):
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        stale = (now - timedelta(seconds=2000)).isoformat()
        log = self._log_with_progress(tmp_path, stale)
        decision = decide(
            config=SupervisorConfig(cadence_seconds=300, tolerance_intervals=3),
            state=SupervisorState(),
            run_log=log,
            process_checker=lambda: True,
            clock=_fixed_clock(now),
        )
        assert decision.liveness == Liveness.DOZING
        assert decision.action == SupervisorAction.REVIVE

    def test_escalate_after_max_revives(self, tmp_path: Path):
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        log = self._log_with_progress(tmp_path, now.isoformat())
        state = SupervisorState(revive_count=3)
        decision = decide(
            config=SupervisorConfig(cadence_seconds=300, max_revives=3),
            state=state,
            run_log=log,
            process_checker=lambda: False,
            clock=_fixed_clock(now),
        )
        assert decision.action == SupervisorAction.ESCALATE_HUMAN

    def test_apply_revive_increments(self):
        state = apply_revive(SupervisorState(revive_count=1))
        assert state.revive_count == 2
