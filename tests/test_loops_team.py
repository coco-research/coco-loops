"""Tests for hire/delegate: governed, propose-only delegation across a team."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coco_loops import lifetime
from coco_loops.paths import kill_switch_path, reset_config
from coco_loops.run_log import RunLog
from coco_loops.team import Subtask, Team, delegate, hire
from coco_loops.verifier import ProposalVerdict, Verdict

LOOP = "team-loop"


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


def _writer(filename: str):
    def _maker(task: dict, repo: Path) -> str:
        (repo / filename).write_text(f"# {task['id']}\n", encoding="utf-8")
        return f"wrote {filename}"

    return _maker


def _approve(r, t, f, d):
    return ProposalVerdict(Verdict.APPROVE, "ok")


def _entries(state: Path):
    return RunLog(state / "run-log.jsonl").read()


class TestMakerRouting:
    def test_maker_for_role_default_and_fallback(self):
        fe, be, dflt = object(), object(), object()
        team = Team(members={"frontend": fe, "backend": be, "default": dflt})
        assert team.maker_for("frontend") is fe
        assert team.maker_for("unknown") is dflt  # falls back to default

    def test_maker_for_falls_back_to_any_without_default(self):
        only = object()
        team = Team(members={"solo": only})
        assert team.maker_for("nope") is only

    def test_hire_builds_one_maker_per_role(self):
        team = hire(["frontend", "backend"])
        assert set(team.roles) == {"frontend", "backend"}
        assert all(callable(m) for m in team.members.values())


class TestDelegate:
    def test_runs_each_subtask_role_routed_and_reverts(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        team = Team(members={"fe": _writer("fe.txt"), "be": _writer("be.txt")})
        subtasks = [
            Subtask(id="s1", instruction="do fe", role="fe"),
            Subtask(id="s2", instruction="do be", role="be"),
        ]

        results = delegate(
            {"id": "parent", "objective": "ship it"},
            subtasks,
            loop_id=LOOP,
            repo=git_repo,
            team=team,
            verifier_fn=_approve,
            run_log_path=state_env / "run-log.jsonl",
        )

        assert [r.outcome for r in results] == ["fix-proposed", "fix-proposed"]
        # propose-only: both makers' files reverted
        assert not (git_repo / "fe.txt").exists()
        assert not (git_repo / "be.txt").exists()
        # run-log has one entry per subtask, tagged parent:subtask + loop_id
        entries = _entries(state_env)
        assert len(entries) == 2
        assert entries[0].loop_id == LOOP
        assert any("parent:s1" in e.last_progress for e in entries)
        assert any("parent:s2" in e.last_progress for e in entries)

    def test_delegation_never_commits(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        before = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"], cwd=git_repo, capture_output=True, text=True
        ).stdout.strip()

        delegate(
            {"id": "p"},
            [Subtask(id="s1", instruction="x", role="fe")],
            loop_id=LOOP,
            repo=git_repo,
            team=Team(members={"fe": _writer("fe.txt")}),
            verifier_fn=_approve,
            run_log_path=state_env / "run-log.jsonl",
        )

        after = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"], cwd=git_repo, capture_output=True, text=True
        ).stdout.strip()
        assert before == after

    def test_stops_when_lifetime_expires_mid_batch(self, git_repo, state_env):
        # expire immediately: the first subtask should self-stop, the second never runs
        lifetime.arm(LOOP, 1)
        lifetime.tick(LOOP, 1)

        def _boom(task, repo):
            raise AssertionError("maker must not run once expired")

        results = delegate(
            {"id": "p"},
            [Subtask(id="s1", instruction="a"), Subtask(id="s2", instruction="b")],
            loop_id=LOOP,
            repo=git_repo,
            team=Team(members={"default": _boom}),
            verifier_fn=_approve,
            run_log_path=state_env / "run-log.jsonl",
        )
        assert len(results) == 1
        assert results[0].outcome == "expired"
        assert kill_switch_path(LOOP).exists()

    def test_stops_when_kill_switch_set(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        kill_switch_path(LOOP).touch()

        def _boom(task, repo):
            raise AssertionError("maker must not run when paused")

        results = delegate(
            {"id": "p"},
            [Subtask(id="s1", instruction="a"), Subtask(id="s2", instruction="b")],
            loop_id=LOOP,
            repo=git_repo,
            team=Team(members={"default": _boom}),
            verifier_fn=_approve,
            run_log_path=state_env / "run-log.jsonl",
        )
        assert len(results) == 1
        assert results[0].outcome == "paused"
