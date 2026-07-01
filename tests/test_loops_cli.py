"""Behavioral tests for the human-facing loops CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coco_loops.cli import main
from coco_loops import lifetime
from coco_loops.paths import reset_config


def _write_contract(loops_dir: Path, loop_id: str, *, level_hint: str = "L1") -> None:
    loops_dir.mkdir(parents=True, exist_ok=True)
    (loops_dir / f"{loop_id}.contract.md").write_text(
        f"# {loop_id}\n\nPhase P1 maturity: **{level_hint}** report-only.\n",
        encoding="utf-8",
    )


def _append_run_log(path: Path, loop_id: str, **overrides) -> None:
    record = {
        "run_id": overrides.get("run_id", "run-1"),
        "loop_id": loop_id,
        "timestamp": overrides.get("timestamp", "2026-06-30T12:00:00+00:00"),
        "items_found": overrides.get("items_found", 1),
        "actions_taken": overrides.get("actions_taken", ["verifier:dry-run REJECT"]),
        "escalations": overrides.get("escalations", []),
        "tokens_estimate": overrides.get("tokens_estimate", 4200),
        "outcome": overrides.get("outcome", "fix-proposed"),
        "human_verdict": overrides.get("human_verdict", None),
        "last_progress": overrides.get("last_progress", "proposal pending review"),
        "verifier_verdict": overrides.get("verifier_verdict", "REJECT"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    loops_dir = tmp_path / "loops"
    runtime_dir = tmp_path / "coco"
    runtime_dir.mkdir()
    run_log = runtime_dir / "loop-run-log.jsonl"
    monkeypatch.setenv("COCO_LOOPS_DIR", str(loops_dir))
    monkeypatch.setenv("COCO_LOOPS_HOME", str(runtime_dir))
    monkeypatch.setenv("COCO_LOOP_RUN_LOG", str(run_log))
    reset_config()
    yield {
        "loops_dir": loops_dir,
        "runtime_dir": runtime_dir,
        "run_log": run_log,
    }
    reset_config()


class TestCliList:
    def test_list_shows_contract_level_and_pause_state(self, cli_env, capsys):
        _write_contract(cli_env["loops_dir"], "synthetic-loop", level_hint="L2")
        (cli_env["runtime_dir"] / "synthetic-loop.disabled").write_text("", encoding="utf-8")

        rc = main(["list"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "synthetic-loop" in out
        assert "L2" in out
        assert "paused" in out

    def test_list_running_when_kill_switch_absent(self, cli_env, capsys):
        _write_contract(cli_env["loops_dir"], "synthetic-loop")

        rc = main(["list"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "running" in out


class TestCliStart:
    def test_start_arms_lifetime_and_clears_kill_switch(self, cli_env, capsys):
        loop_id = "synthetic-loop"
        _write_contract(cli_env["loops_dir"], loop_id)
        (cli_env["runtime_dir"] / f"{loop_id}.disabled").write_text("", encoding="utf-8")

        rc = main(["start", loop_id, "--for", "1h"])
        out = capsys.readouterr().out

        assert rc == 0
        assert f"armed {loop_id} for 1h (kill-switch cleared)" in out
        assert lifetime.used_seconds(loop_id, state_dir=cli_env["runtime_dir"]) == 0
        assert lifetime.cap_seconds(loop_id, state_dir=cli_env["runtime_dir"]) == 3600
        assert not (cli_env["runtime_dir"] / f"{loop_id}.disabled").exists()


class TestCliStatus:
    def test_status_reports_run_log_and_budget(self, cli_env, capsys):
        _write_contract(cli_env["loops_dir"], "synthetic-loop")
        _append_run_log(cli_env["run_log"], "synthetic-loop")

        rc = main(["status", "synthetic-loop"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "Run-log: 1 entry" in out
        assert "fix-proposed" in out
        assert "proposal pending review" in out
        assert "verifier=REJECT" in out
        assert "Budget: caps not configured" in out

    def test_status_no_entries_message(self, cli_env, capsys):
        _write_contract(cli_env["loops_dir"], "synthetic-loop")

        rc = main(["status", "synthetic-loop"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "no entries yet" in out

    def test_status_shows_lifetime_line(self, cli_env, capsys):
        loop_id = "synthetic-loop"
        _write_contract(cli_env["loops_dir"], loop_id)
        lifetime.arm(loop_id, 3600, state_dir=cli_env["runtime_dir"])
        lifetime.tick(loop_id, 600, state_dir=cli_env["runtime_dir"])

        rc = main(["status", loop_id])
        out = capsys.readouterr().out

        assert rc == 0
        assert "Lifetime: 10m of 1h used (50m left)" in out

    def test_status_shows_unbounded_lifetime(self, cli_env, capsys):
        _write_contract(cli_env["loops_dir"], "synthetic-loop")

        rc = main(["status", "synthetic-loop"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "Lifetime: unbounded" in out


class TestCliPromote:
    def test_promote_fails_without_artifacts(self, cli_env, capsys):
        _write_contract(cli_env["loops_dir"], "synthetic-loop")

        rc = main(["promote", "synthetic-loop"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "FAIL" in out
        assert "Missing budget artifact" in out or "Missing run-log artifact" in out

    def test_promote_passes_with_synthetic_artifacts(self, cli_env, capsys):
        loop_id = "synthetic-loop"
        _write_contract(cli_env["loops_dir"], loop_id)
        (cli_env["loops_dir"] / "loop-budget.md").write_text("# budget\n", encoding="utf-8")
        _append_run_log(
            cli_env["run_log"],
            loop_id,
            human_verdict="correct",
            actions_taken=[],
            outcome="report-only",
        )
        (cli_env["runtime_dir"] / "verifier-proven.json").write_text(
            json.dumps({"verifier_proven": True}),
            encoding="utf-8",
        )

        rc = main(["promote", loop_id, "--comprehension-signed-off"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "PASS" in out
