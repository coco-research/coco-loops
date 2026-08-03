"""Regression tests for two defects found running the loop for real.

Both were silent failures of the propose-only contract rather than crashes, which is why
they survived: the loop reported honest outcomes while quietly losing the work.

1. A cycle that ended without a verdict still consumed its task, so running against a
   broken maker drained the whole queue having done nothing. An operator who then fixed
   the maker restarted against an empty queue and concluded the work was done.
2. A proposal was discarded on every verdict, because the tree is reverted after each
   cycle and the diff was only ever held in memory. A propose-only loop whose proposals
   are destroyed produces no reviewable output at all.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from coco_loops.runner import (
    MakerAuthError,
    MakerError,
    RunnerConfig,
    file_task_source,
    run_cycle,
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return repo


def _queue(tmp_path: Path, *ids: str) -> Path:
    q = tmp_path / "tasks.json"
    q.write_text(json.dumps([{"id": i, "name": i, "instruction": f"do {i}"} for i in ids]))
    return q


def _cfg(tmp_path: Path, repo: Path, queue: Path, maker, **kw) -> RunnerConfig:
    source = file_task_source(queue)
    return RunnerConfig(
        loop_id="t",
        repo=repo,
        task_source=source,
        maker=maker,
        run_log_path=tmp_path / "run-log.jsonl",
        requeue=getattr(source, "requeue", None),
        max_cycles=1,
        **kw,
    )


def _ids(queue: Path) -> list[str]:
    return [t["id"] for t in json.loads(queue.read_text())]


class TestRequeueOnUnjudgedOutcome:
    def test_auth_failure_returns_the_task(self, tmp_path):
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a", "b")

        def maker(task, repo_path):
            raise MakerAuthError("not authenticated", stderr="Authentication required")

        result = run_cycle(_cfg(tmp_path, repo, queue, maker))
        assert result.outcome == "blocked"
        # The whole point: the task is still there to be worked once auth is fixed.
        assert _ids(queue) == ["a", "b"]

    def test_maker_error_returns_the_task(self, tmp_path):
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a")

        def maker(task, repo_path):
            raise MakerError("exit 127", stderr="command not found")

        result = run_cycle(_cfg(tmp_path, repo, queue, maker))
        assert result.outcome == "maker-error"
        assert _ids(queue) == ["a"]

    def test_unexpected_error_returns_the_task(self, tmp_path):
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a")

        def maker(task, repo_path):
            raise FileNotFoundError("loop-constitution.md")

        result = run_cycle(_cfg(tmp_path, repo, queue, maker))
        assert result.outcome == "error"
        assert _ids(queue) == ["a"]

    def test_requeue_preserves_order_and_does_not_duplicate(self, tmp_path):
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a", "b", "c")

        def maker(task, repo_path):
            raise MakerError("boom", stderr="boom")

        for _ in range(3):
            run_cycle(_cfg(tmp_path, repo, queue, maker))
        # Three failed cycles must not drain, reorder or duplicate the queue.
        assert _ids(queue) == ["a", "b", "c"]

    def test_a_judged_task_is_still_consumed(self, tmp_path):
        """Requeue must not resurrect work that actually got a verdict."""
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a", "b")

        def maker(task, repo_path):
            (repo / "changed.txt").write_text("edit\n")
            return "wrote changed.txt"

        result = run_cycle(_cfg(tmp_path, repo, queue, maker))
        assert result.outcome == "fix-proposed"
        assert _ids(queue) == ["b"]


class TestProposalsArePersisted:
    def test_diff_is_saved_even_when_rejected(self, tmp_path):
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a")
        proposals = tmp_path / "proposals"

        def maker(task, repo_path):
            (repo / "note.md").write_text("a documentation-only change\n")
            return "wrote note.md"

        result = run_cycle(
            _cfg(tmp_path, repo, queue, maker, proposals_dir=proposals)
        )
        assert result.outcome == "fix-proposed"
        # A docs-only change has no test surface, so the verifier rejects it. The proposal
        # must survive anyway, or a propose-only run leaves nothing to review.
        assert result.verdict == "REJECT"
        patches = list(proposals.glob("*.patch"))
        assert len(patches) == 1, f"expected one saved patch, found {patches}"
        body = patches[0].read_text()
        assert "note.md" in body
        assert "a documentation-only change" in body
        assert result.proposal_path == str(patches[0])

    def test_the_tree_is_still_reverted(self, tmp_path):
        """Saving the diff must not weaken the propose-only guarantee."""
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a")

        def maker(task, repo_path):
            (repo / "note.md").write_text("x\n")
            return "wrote note.md"

        run_cycle(_cfg(tmp_path, repo, queue, maker, proposals_dir=tmp_path / "p"))
        assert not (repo / "note.md").exists(), "propose-only loop must revert the tree"
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        assert dirty == ""

    def test_no_patch_written_when_the_maker_changed_nothing(self, tmp_path):
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a")
        proposals = tmp_path / "proposals"

        def maker(task, repo_path):
            return "considered it, changed nothing"

        result = run_cycle(_cfg(tmp_path, repo, queue, maker, proposals_dir=proposals))
        assert result.outcome == "no-op"
        assert not proposals.exists() or list(proposals.glob("*.patch")) == []

    def test_defaults_beside_the_run_log_when_no_dir_given(self, tmp_path):
        repo, queue = _repo(tmp_path), _queue(tmp_path, "a")

        def maker(task, repo_path):
            (repo / "note.md").write_text("y\n")
            return "wrote note.md"

        run_cycle(_cfg(tmp_path, repo, queue, maker))
        assert list((tmp_path / "proposals").glob("*.patch"))
