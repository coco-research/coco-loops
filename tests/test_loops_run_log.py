"""Tests for append-only loop run-log."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coco_loops.run_log import RunLog, RunLogEntry


def _entry(**overrides) -> RunLogEntry:
    base = {
        "run_id": "run-1",
        "items_found": 2,
        "actions_taken": ["report"],
        "escalations": [],
        "tokens_estimate": 1200,
        "outcome": "report-only",
        "human_verdict": "correct",
        "last_progress": "2026-06-30T12:00:00+00:00",
    }
    base.update(overrides)
    return RunLogEntry(**base)


class TestRunLogAppendRead:
    def test_append_writes_all_required_fields(self, tmp_path: Path):
        log_path = tmp_path / "run-log.jsonl"
        log = RunLog(log_path)
        record = log.append(_entry())
        assert record.run_id == "run-1"
        assert record.items_found == 2
        assert record.human_verdict == "correct"

        raw = log_path.read_text(encoding="utf-8").strip()
        data = json.loads(raw)
        for field in (
            "run_id",
            "items_found",
            "actions_taken",
            "escalations",
            "tokens_estimate",
            "outcome",
            "human_verdict",
            "last_progress",
        ):
            assert field in data

    def test_read_returns_appended_entries(self, tmp_path: Path):
        log = RunLog(tmp_path / "run-log.jsonl")
        log.append(_entry(run_id="a"))
        log.append(_entry(run_id="b", items_found=0))
        entries = log.read()
        assert len(entries) == 2
        assert entries[0].run_id == "a"
        assert entries[1].run_id == "b"

    def test_last_progress_helper(self, tmp_path: Path):
        log = RunLog(tmp_path / "run-log.jsonl")
        log.append(_entry(last_progress="2026-06-30T10:00:00+00:00"))
        log.append(_entry(last_progress="2026-06-30T11:00:00+00:00"))
        assert log.last_progress() == "2026-06-30T11:00:00+00:00"


class TestRunLogPrune:
    def test_prune_drops_old_entries(self, tmp_path: Path):
        log_path = tmp_path / "run-log.jsonl"
        log = RunLog(log_path, retention_days=30)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        log.append(_entry(run_id="old", timestamp=old_ts, last_progress=old_ts))
        log.append(_entry(run_id="fresh", timestamp=fresh_ts, last_progress=fresh_ts))

        dropped = log.prune()
        assert dropped == 1
        remaining = log.read()
        assert len(remaining) == 1
        assert remaining[0].run_id == "fresh"

    def test_missing_required_field_raises(self, tmp_path: Path):
        log = RunLog(tmp_path / "run-log.jsonl")
        with pytest.raises(ValueError, match="missing required fields"):
            log.append({"run_id": "x", "items_found": 1})
