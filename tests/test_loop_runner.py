"""Tests for the generic governed loop runner (propose-only guarantees)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from coco_loops import lifetime, runner
from coco_loops.cli import main
from coco_loops.paths import kill_switch_path, reset_config
from coco_loops.run_log import RunLog
from coco_loops.runner import (
    CycleResult,
    MakerAuthError,
    MakerError,
    PreflightResult,
    RunnerConfig,
    cli_agent_maker,
    cursor_agent_maker,
    cursor_agent_preflight,
    generic_binary_preflight,
    maker_preflight_gate,
    run,
    run_cycle,
)
from coco_loops.verifier import ProposalVerdict, Verdict

LOOP = "demo-loop"

CURSOR_AGENT_ON_PATH = shutil.which("cursor-agent") is not None


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


class TestMakerFailureOutcomes:
    """Defect 1: a maker that never actually ran must not be logged as 'no-op'."""

    def test_maker_auth_error_is_blocked_not_noop(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)

        def _unauthenticated(task, repo):
            raise MakerAuthError(
                "cursor-agent exited 1: Error: Authentication required.",
                returncode=1,
                stderr="Error: Authentication required. Please run 'agent login' first.",
            )

        result = run_cycle(
            _cfg(git_repo, state_env, maker=_unauthenticated, task_source=_one_task())
        )

        assert result.outcome == "blocked"
        assert result.outcome != "no-op"
        assert "Authentication required" in result.detail
        entry = _entries(state_env)[0]
        assert entry.outcome == "blocked"
        # the real stderr is visible in the run-log, not swallowed
        assert any("Authentication required" in a for a in entry.actions_taken)
        assert entry.escalations  # operator attention flagged

    def test_maker_non_auth_error_is_maker_error_not_noop(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)

        def _crashes(task, repo):
            raise MakerError("cursor-agent exited 127: command not found", returncode=127, stderr="command not found")

        result = run_cycle(_cfg(git_repo, state_env, maker=_crashes, task_source=_one_task()))

        assert result.outcome == "maker-error"
        assert result.outcome != "no-op"
        assert "command not found" in result.detail
        assert _entries(state_env)[0].outcome == "maker-error"

    def test_maker_error_still_reverts_and_ticks_lifetime(self, git_repo, state_env, monkeypatch):
        lifetime.arm(LOOP, 3600)
        seq = iter([2000.0, 2007.0])
        monkeypatch.setattr(runner.time, "monotonic", lambda: next(seq))

        def _writes_then_fails(task, repo):
            (repo / "half.py").write_text("boom\n", encoding="utf-8")
            raise MakerError("boom", stderr="boom")

        run_cycle(_cfg(git_repo, state_env, maker=_writes_then_fails, task_source=_one_task()))

        assert not (git_repo / "half.py").exists()  # still propose-only: reverted
        assert lifetime.used_seconds(LOOP) == 7  # active-work time still counted

    def test_genuine_noop_is_unaffected(self, git_repo, state_env):
        """Control: a maker that runs fine and makes no change is still a no-op."""
        lifetime.arm(LOOP, 3600)
        result = run_cycle(
            _cfg(git_repo, state_env, maker=lambda t, r: "looked, nothing to change", task_source=_one_task())
        )
        assert result.outcome == "no-op"


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


class TestMakerPreflight:
    """Defect 2: refuse to start (and burn no lifetime) before the first cycle."""

    def test_gate_none_when_no_preflight_configured(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        cfg = _cfg(git_repo, state_env, maker=_writer_maker(), task_source=_one_task())
        assert maker_preflight_gate(cfg) is None

    def test_gate_passes_through_when_preflight_ok(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        cfg = _cfg(
            git_repo,
            state_env,
            maker=_writer_maker(),
            task_source=_one_task(),
            maker_preflight=lambda: PreflightResult(ok=True, detail="fine"),
        )
        assert maker_preflight_gate(cfg) is None

    def test_gate_blocks_and_logs_without_touching_lifetime_or_task_source(
        self, git_repo, state_env
    ):
        lifetime.arm(LOOP, 3600)
        task_source_calls = {"n": 0}

        def _boom_if_called():
            task_source_calls["n"] += 1
            return {"id": "t1", "name": "t"}

        def _boom_maker(task, repo):
            raise AssertionError("maker must not run when pre-flight failed")

        cfg = _cfg(
            git_repo,
            state_env,
            maker=_boom_maker,
            task_source=_boom_if_called,
            maker_preflight=lambda: PreflightResult(
                ok=False, detail="not authenticated", hint="run `cursor-agent login`"
            ),
        )

        gate = maker_preflight_gate(cfg)

        assert gate is not None
        assert gate.outcome == "blocked"
        assert "not authenticated" in gate.detail
        assert "cursor-agent login" in gate.detail
        assert task_source_calls["n"] == 0  # never looked at the queue
        assert lifetime.used_seconds(LOOP) == 0  # no active-work time spent
        entry = _entries(state_env)[0]
        assert entry.outcome == "blocked"
        assert entry.escalations

    def test_run_refuses_before_first_cycle_when_preflight_fails(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        cfg = _cfg(
            git_repo,
            state_env,
            maker=_writer_maker(),
            task_source=_one_task(),
            maker_preflight=lambda: PreflightResult(ok=False, detail="no auth"),
            max_cycles=5,
        )

        results = run(cfg)

        assert len(results) == 1
        assert results[0].outcome == "blocked"
        assert lifetime.used_seconds(LOOP) == 0


class TestMakerErrorCircuitBreaker:
    """Defect 4: N consecutive maker failures self-pause instead of grinding on."""

    def test_trips_after_n_consecutive_maker_errors(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)

        def _always_fails(task, repo):
            raise MakerError("cursor-agent exited 1", stderr="boom")

        cfg = _cfg(
            git_repo,
            state_env,
            maker=_always_fails,
            task_source=lambda: {"id": "t", "name": "t"},  # always a task available
            max_cycles=10,
            maker_error_circuit_breaker=3,
        )

        results = run(cfg)

        # 3 maker-error cycles, then the breaker trips and appends a 4th "paused"
        # result — it must NOT grind through all 10 max_cycles.
        assert len(results) == 4
        assert [r.outcome for r in results[:3]] == ["maker-error", "maker-error", "maker-error"]
        assert results[3].outcome == "paused"
        assert kill_switch_path(LOOP).exists()

    def test_resets_on_a_success_between_failures(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)
        calls = {"n": 0}

        def _flaky(task, repo):
            calls["n"] += 1
            if calls["n"] in (1, 3):  # fails once, then again later — never two in a row
                raise MakerError("fail", stderr="fail")
            (repo / f"ok{calls['n']}.py").write_text("print(1)\n", encoding="utf-8")
            return f"wrote ok{calls['n']}.py"

        cfg = _cfg(
            git_repo,
            state_env,
            maker=_flaky,
            task_source=lambda: {"id": "t", "name": "t"},
            max_cycles=5,
            maker_error_circuit_breaker=2,
        )

        results = run(cfg)

        # A single failure between successes never accumulates to the breaker
        # threshold (2 *consecutive* failures) — every one of the 5 cycles runs.
        assert len(results) == 5
        assert [r.outcome for r in results] == [
            "maker-error",
            "fix-proposed",
            "maker-error",
            "fix-proposed",
            "fix-proposed",
        ]
        assert not kill_switch_path(LOOP).exists()

    def test_breaker_disabled_when_none(self, git_repo, state_env):
        lifetime.arm(LOOP, 3600)

        def _always_fails(task, repo):
            raise MakerError("fail", stderr="fail")

        cfg = _cfg(
            git_repo,
            state_env,
            maker=_always_fails,
            task_source=lambda: {"id": "t", "name": "t"},
            max_cycles=4,
            maker_error_circuit_breaker=None,
        )

        results = run(cfg)

        assert len(results) == 4  # grinds through every cycle; breaker is off
        assert all(r.outcome == "maker-error" for r in results)
        assert not kill_switch_path(LOOP).exists()


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
    def test_run_once_noop_when_no_tasks(self, git_repo, state_env, capsys, monkeypatch):
        # missing task queue -> no-op, so the cursor-agent maker is never invoked.
        # Stub the pre-flight probe too, so this test never shells out to a real
        # cursor-agent process (hermetic regardless of what's on the test machine).
        monkeypatch.setattr(
            runner,
            "cursor_agent_preflight",
            lambda: PreflightResult(ok=True, detail="stub: skipped in test"),
        )
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

    def test_run_once_blocked_when_preflight_fails(self, git_repo, state_env, capsys, monkeypatch):
        """The decisive CLI-level guarantee: a broken maker refuses to start, does
        not touch the lifetime counter, and never prints a bare 'no-op'."""
        monkeypatch.setattr(
            runner,
            "cursor_agent_preflight",
            lambda: PreflightResult(
                ok=False,
                detail="cursor-agent is not authenticated",
                hint="Fix: run `cursor-agent login`.",
            ),
        )
        lifetime.arm(LOOP, 3600)
        # A real task is queued (unlike the no-op test above) — the pre-flight must
        # refuse BEFORE ever looking at the task queue or invoking the maker.
        tasks_path = state_env / f"{LOOP}-tasks.json"
        tasks_path.write_text(
            json.dumps([{"id": "t1", "name": "delete a directory", "instruction": "do it"}]),
            encoding="utf-8",
        )

        rc = main(
            ["run", LOOP, "--once", "--repo", str(git_repo), "--tasks", str(tasks_path)]
        )
        out_err = capsys.readouterr()

        assert rc == 1
        assert "blocked" in out_err.err
        assert "no-op" not in out_err.err
        assert "cursor-agent login" in out_err.err
        # lifetime budget untouched: pre-flight failure must not burn active-work time
        assert lifetime.used_seconds(LOOP, state_dir=state_env) == 0
        # the task queue was never touched either
        assert json.loads(tasks_path.read_text(encoding="utf-8")) == [
            {"id": "t1", "name": "delete a directory", "instruction": "do it"}
        ]


class TestCliAgentMaker:
    """Unit tests for the pluggable CLI-maker factory (mocked subprocess.run)."""

    def test_success_returns_stdout_summary(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, returncode=0, stdout="did the thing", stderr="")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        maker = cli_agent_maker(["my-agent", "-p"])

        summary = maker({"id": "t1", "name": "task", "instruction": "do it"}, tmp_path)

        assert summary == "did the thing"
        assert calls[0][0] == "my-agent"
        assert calls[0][-1].startswith("Task: task")  # prompt is the trailing arg

    def test_nonzero_exit_with_auth_text_raises_maker_auth_error(self, tmp_path, monkeypatch):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv,
                returncode=1,
                stdout="",
                stderr=(
                    "Error: Authentication required. Please run 'agent login' first, "
                    "or set CURSOR_API_KEY environment variable."
                ),
            )

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        maker = cursor_agent_maker()  # uses cli_agent_maker under the hood

        with pytest.raises(MakerAuthError) as excinfo:
            maker({"id": "t1", "name": "task"}, tmp_path)

        assert "Authentication required" in excinfo.value.stderr
        assert excinfo.value.returncode == 1

    def test_nonzero_exit_without_auth_text_raises_generic_maker_error(self, tmp_path, monkeypatch):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, returncode=127, stdout="", stderr="segfault")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        maker = cli_agent_maker(["my-agent"])

        with pytest.raises(MakerError) as excinfo:
            maker({"id": "t1", "name": "task"}, tmp_path)

        assert not isinstance(excinfo.value, MakerAuthError)
        assert "segfault" in excinfo.value.stderr

    def test_missing_binary_raises_maker_error(self, tmp_path, monkeypatch):
        def fake_run(argv, **kwargs):
            raise FileNotFoundError(f"No such file: {argv[0]}")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        maker = cli_agent_maker(["nonexistent-agent-xyz"])

        with pytest.raises(MakerError) as excinfo:
            maker({"id": "t1", "name": "task"}, tmp_path)

        assert "not found on PATH" in str(excinfo.value)

    def test_timeout_raises_maker_error(self, tmp_path, monkeypatch):
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        maker = cli_agent_maker(["slow-agent"], timeout=1)

        with pytest.raises(MakerError) as excinfo:
            maker({"id": "t1", "name": "task"}, tmp_path)

        assert "timed out" in str(excinfo.value)


class TestGenericBinaryPreflight:
    def test_ok_when_found(self, monkeypatch):
        monkeypatch.setattr(runner.shutil, "which", lambda b: f"/usr/bin/{b}")
        result = generic_binary_preflight("my-agent")
        assert result.ok is True

    def test_not_ok_when_missing(self, monkeypatch):
        monkeypatch.setattr(runner.shutil, "which", lambda b: None)
        result = generic_binary_preflight("nonexistent-agent-xyz")
        assert result.ok is False
        assert "nonexistent-agent-xyz" in result.detail


class TestCursorAgentPreflight:
    """Unit tests for the cheap cursor-agent status probe (mocked subprocess.run)."""

    def test_authenticated_is_ok(self, monkeypatch):
        def fake_run(argv, **kwargs):
            payload = {"status": "authenticated", "isAuthenticated": True, "message": "Logged in"}
            return subprocess.CompletedProcess(argv, returncode=0, stdout=json.dumps(payload), stderr="")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = cursor_agent_preflight()
        assert result.ok is True

    def test_unauthenticated_is_blocked_with_login_hint(self, monkeypatch):
        def fake_run(argv, **kwargs):
            payload = {"status": "unauthenticated", "isAuthenticated": False, "message": "Not logged in"}
            return subprocess.CompletedProcess(argv, returncode=0, stdout=json.dumps(payload), stderr="")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = cursor_agent_preflight()
        assert result.ok is False
        assert "Not logged in" in result.detail
        assert "cursor-agent login" in result.hint

    def test_missing_binary_is_blocked(self, monkeypatch):
        def fake_run(argv, **kwargs):
            raise FileNotFoundError("no cursor-agent")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = cursor_agent_preflight()
        assert result.ok is False
        assert "not on PATH" in result.detail

    def test_nonzero_status_exit_is_blocked(self, monkeypatch):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = cursor_agent_preflight()
        assert result.ok is False
        assert "boom" in result.detail

    def test_unparseable_output_without_auth_text_does_not_block(self, monkeypatch):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, returncode=0, stdout="not json at all", stderr="")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = cursor_agent_preflight()
        assert result.ok is True  # never block a loop on a future parsing quirk

    def test_timeout_is_blocked(self, monkeypatch):
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=15)

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = cursor_agent_preflight()
        assert result.ok is False
        assert "did not respond" in result.detail


@pytest.mark.skipif(not CURSOR_AGENT_ON_PATH, reason="cursor-agent not installed on this machine")
class TestCursorAgentRealAuthFailure:
    """The decisive, non-mocked demonstration.

    Points the real cursor-agent binary at an empty HOME (no stored credentials,
    CURSOR_API_KEY unset) to reproduce a genuine 'not authenticated' failure without
    touching this machine's real login state (nothing under the real $HOME/.cursor
    is read or written; monkeypatch restores the real HOME after each test).
    """

    def test_real_preflight_detects_unauthenticated(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)

        result = cursor_agent_preflight()

        assert result.ok is False
        assert "cursor-agent login" in result.hint

    def test_real_maker_raises_auth_error_not_a_silent_failure(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        repo = tmp_path / "repo"
        repo.mkdir()

        maker = cursor_agent_maker(timeout=30)

        with pytest.raises(MakerAuthError) as excinfo:
            maker({"id": "t1", "name": "delete-a-dir", "instruction": "irrelevant"}, repo)

        assert "Authentication required" in excinfo.value.stderr

    def test_real_run_cycle_reports_blocked_not_bare_noop(self, git_repo, state_env, monkeypatch):
        fake_home = git_repo.parent / "fake-home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        lifetime.arm(LOOP, 3600)

        cfg = _cfg(
            git_repo,
            state_env,
            maker=cursor_agent_maker(timeout=30),
            task_source=_one_task(),
        )

        result = run_cycle(cfg)

        assert result.outcome == "blocked"
        assert result.outcome != "no-op"
        assert "Authentication required" in result.detail
        entry = _entries(state_env)[0]
        assert entry.outcome == "blocked"
        assert any("Authentication required" in a for a in entry.actions_taken)
