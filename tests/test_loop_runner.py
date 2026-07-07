"""Tests for the generic governed loop runner (propose-only guarantees)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from coco_loops import lifetime, runner
from coco_loops.cli import main
from coco_loops.paths import kill_switch_path, reset_config
from coco_loops.run_log import RunLog
from coco_loops.runner import CycleResult, RunnerConfig, run, run_cycle
from coco_loops.verifier import ProposalVerdict, Verdict

LOOP = "demo-loop"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def state_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("COCO_LOOPS_HOME", str(state))
    monkeypatch.setenv("COCO_LOOP_RUN_LOG", str(state / "run-log.jsonl"))
    reset_config()
    yield state
    reset_config()


def _cfg(repo: Path, state: Path, *, maker, task_source, verifier_fn=None, **kw) -> RunnerConfig:
    return RunnerConfig(
        loop_id=LOOP,
        repo=repo,
        task_source=task_source,
        maker=maker,
        verifier_fn=verifier_fn or (lambda r, t, f, d: ProposalVerdict(Verdict.APPROVE, "ok")),
        run_log_path=state / "run-log.jsonl",
        poll_interval_seconds=0,
        **kw,
    )


def _one_task():
    given = {"n": 0}

    def _src():
        given["n"] += 1
        return {"id": "t1", "name": "fix a thing", "instruction": "do it"} if given["n"] == 1 else None

    return _src


def _writer_maker(filename: str = "new_file.py", body: str = "print('hi')\n"):
    def _maker(task: dict, repo: Path) -> str:
        (repo / filename).write_text(body, encoding="utf-8")
        return f"wrote {filename}"

    return _maker


def _entries(state: Path):
    return RunLog(state / "run-log.jsonl").read()


class TestProposeOnly:
    def test_reverts_tree_logs_and_tags_loop_id(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        cfg = _cfg(git_repo, state_env, maker=_writer_maker(), task_source=_one_task())

        result = run_cycle(cfg)

        assert result.outcome == "fix-proposed"
        assert result.verdict == "APPROVE"
        # tree reverted clean: the maker's file is gone
        assert not (git_repo / "new_file.py").exists()
        assert runner._changed_files(git_repo) == []
        # run-log entry exists and is tagged with the loop_id (the framework fix)
        entries = _entries(state_env)
        assert len(entries) == 1
        assert entries[0].loop_id == LOOP
        assert entries[0].outcome == "fix-proposed"

    def test_never_commits_at_l1(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        before = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"], cwd=git_repo, capture_output=True, text=True
        ).stdout.strip()

        run_cycle(_cfg(git_repo, state_env, maker=_writer_maker(), task_source=_one_task()))

        after = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"], cwd=git_repo, capture_output=True, text=True
        ).stdout.strip()
        assert before == after  # propose-only: no commit

    def test_lifetime_ticked_with_active_work(self, git_repo, state_env, monkeypatch):
        lifetime.arm(LOOP, 3600)
        seq = iter([1000.0, 1005.0])
        monkeypatch.setattr(runner.time, "monotonic", lambda: next(seq))

        run_cycle(_cfg(git_repo, state_env, maker=_writer_maker(), task_source=_one_task()))

        assert lifetime.used_seconds(LOOP) == 5


class TestGovernanceGates:
    def test_kill_switch_pauses_without_calling_maker(self, git_repo, state_env):
        kill_switch_path(LOOP).touch()

        def _boom(task, repo):
            raise AssertionError("maker must not run when paused")

        result = run_cycle(_cfg(git_repo, state_env, maker=_boom, task_source=_one_task()))
        assert result.outcome == "paused"

    def test_expired_lifetime_self_stops(self, git_repo, state_env):
        lifetime.arm(LOOP, 1)
        lifetime.tick(LOOP, 1)  # used >= cap -> expired

        def _boom(task, repo):
            raise AssertionError("maker must not run when expired")

        result = run_cycle(_cfg(git_repo, state_env, maker=_boom, task_source=_one_task()))
        assert result.outcome == "expired"
        assert kill_switch_path(LOOP).exists()  # durable death

    def test_dirty_tree_refuses_and_preserves_changes(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        (git_repo / "uncommitted.txt").write_text("precious\n", encoding="utf-8")

        def _boom(task, repo):
            raise AssertionError("maker must not run on a dirty tree")

        result = run_cycle(_cfg(git_repo, state_env, maker=_boom, task_source=_one_task()))
        assert result.outcome == "paused"
        # the user's uncommitted work is untouched (not clobbered by a revert)
        assert (git_repo / "uncommitted.txt").read_text(encoding="utf-8") == "precious\n"

    def test_no_task_is_noop(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        result = run_cycle(_cfg(git_repo, state_env, maker=_writer_maker(), task_source=lambda: None))
        assert result.outcome == "no-op"

    def test_maker_no_change_is_noop(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        result = run_cycle(
            _cfg(git_repo, state_env, maker=lambda t, r: "did nothing", task_source=_one_task())
        )
        assert result.outcome == "no-op"


class TestVerdicts:
    def test_escalate_human_logs_escalation(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        cfg = _cfg(
            git_repo,
            state_env,
            maker=_writer_maker(),
            task_source=_one_task(),
            verifier_fn=lambda r, t, f, d: ProposalVerdict(Verdict.ESCALATE_HUMAN, "risky path"),
        )
        result = run_cycle(cfg)
        assert result.outcome == "escalated"
        assert _entries(state_env)[0].escalations

    def test_reject_is_still_propose_only(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        cfg = _cfg(
            git_repo,
            state_env,
            maker=_writer_maker(),
            task_source=_one_task(),
            verifier_fn=lambda r, t, f, d: ProposalVerdict(Verdict.REJECT, "tests failed"),
        )
        result = run_cycle(cfg)
        assert result.outcome == "fix-proposed"
        assert result.verdict == "REJECT"
        assert not (git_repo / "new_file.py").exists()  # reverted regardless

    def test_maker_error_is_honest_and_reverts(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)

        def _writes_then_raises(task, repo):
            (repo / "half.py").write_text("boom\n", encoding="utf-8")
            raise RuntimeError("maker blew up")

        result = run_cycle(_cfg(git_repo, state_env, maker=_writes_then_raises, task_source=_one_task()))
        assert result.outcome == "error"
        assert not (git_repo / "half.py").exists()  # tree still reverted on exception
        assert _entries(state_env)[0].outcome == "error"


class TestRunDriver:
    def test_run_stops_on_expired(self, git_repo, state_env):
        lifetime.arm(LOOP, 1)
        lifetime.tick(LOOP, 1)
        results = run(_cfg(git_repo, state_env, maker=_writer_maker(), task_source=_one_task(), max_cycles=5))
        assert len(results) == 1
        assert results[0].outcome == "expired"

    def test_run_respects_max_cycles(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        # task source always returns a task; maker makes no change -> no-op each cycle
        cfg = _cfg(
            git_repo,
            state_env,
            maker=lambda t, r: "noop",
            task_source=lambda: {"id": "t", "name": "t"},
            max_cycles=3,
        )
        results = run(cfg)
        assert len(results) == 3
        assert all(r.outcome == "no-op" for r in results)


class TestFileTaskSource:
    def test_pops_tasks_then_none(self, tmp_path):
        queue = tmp_path / "tasks.json"
        queue.write_text(json.dumps([{"id": "a"}, {"id": "b"}]), encoding="utf-8")
        src = runner.file_task_source(queue)
        assert src()["id"] == "a"
        assert src()["id"] == "b"
        assert src() is None
        assert json.loads(queue.read_text(encoding="utf-8")) == []

    def test_missing_file_is_none(self, tmp_path):
        assert runner.file_task_source(tmp_path / "nope.json")() is None


class TestCliRun:
    def test_run_once_noop_when_no_tasks(self, git_repo, state_env, capsys):
        # missing task queue -> no-op, so the cursor-agent maker is never invoked
        lifetime.arm(LOOP, 3600)
        rc = main(
            [
                "run",
                LOOP,
                "--once",
                "--repo",
                str(git_repo),
                "--tasks",
                str(state_env / "no-such-tasks.json"),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "no-op" in out
