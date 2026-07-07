"""Generic governed loop runner.

Runs a chartered loop under the coco-loops governance core. At L1 the runner is
PROPOSE-ONLY: a maker edits the working tree to propose a change for one task, the
separate verifier judges it, the proposal is logged to the run-log, and then the
working tree is REVERTED. Nothing is committed. Commit authority belongs to the
verifier at L2+, gated by the promotion gate, never to this runner.

Safety design:
- The runner refuses to run on a dirty working tree, because its propose-only
  revert (`git restore` + `git clean`) would otherwise clobber uncommitted work.
  The recommended setup is a dedicated worktree per loop, exactly as CoCo's
  coding-loop uses ~/.coco/coding-loop-wt.
- It never trusts the maker's report: it computes the changed files and diff from
  git itself and hands those to the verifier.
- The revert and the lifetime tick both run in a `finally`, so the tree is cleaned
  and active-work time is counted on every exit path, including exceptions (the
  propose-only guarantee proven on CoCo's coding-loop, v3).

This module is stdlib-only (subprocess is stdlib, like the verifier's own test
runner). The default maker shells out to `cursor-agent`, which is sanctioned and
routes through the QuantumBlack gateway, satisfying constitution I3. A different
maker can be injected; tests inject a fake.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from coco_loops import lifetime
from coco_loops.paths import global_pause_path, kill_switch_path, resolve_run_log_path
from coco_loops.run_log import RunLog, RunLogEntry
from coco_loops.verifier import Verdict, verify_proposal

# A maker edits the working tree to propose a change for one task, and returns a
# short human-readable summary. The runner does NOT trust it: the diff is read
# from git afterwards.
Maker = Callable[[dict, Path], str]

# A task source returns the next task dict (with at least an 'id' plus a 'name' or
# 'instruction'), or None when there is nothing to do this cycle.
TaskSource = Callable[[], Optional[dict]]

# A verifier judges (repo, task, files_changed, diff_text) and returns an object
# exposing `.verdict` and `.reasoning`. Defaults to coco_loops.verifier.verify_proposal.
VerifierFn = Callable[[Path, dict, list, str], object]


@dataclass
class RunnerConfig:
    loop_id: str
    repo: Path
    task_source: TaskSource
    maker: Maker
    verifier_fn: VerifierFn = verify_proposal
    run_log_path: Optional[Path] = None
    poll_interval_seconds: float = 60.0
    max_cycles: Optional[int] = None  # None => run until governance stops it


@dataclass
class CycleResult:
    outcome: str  # paused | expired | no-op | fix-proposed | escalated | error
    detail: str = ""
    task_id: Optional[str] = None
    verdict: Optional[str] = None


# --- git helpers: capture the maker's change, then discard it (propose-only) ---

def _git(repo: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=timeout
    )


def _changed_files(repo: Path) -> list[str]:
    proc = _git(repo, "status", "--porcelain")
    files: list[str] = []
    for line in proc.stdout.splitlines():
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:  # rename: keep the destination
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path)
    return files


def _diff_text(repo: Path) -> str:
    # intent-to-add so new (untracked) files appear in the diff for the verifier's
    # raw-cloud-host scan; undone by _revert_tree's reset.
    _git(repo, "add", "-N", ".")
    return _git(repo, "diff").stdout


def _revert_tree(repo: Path) -> None:
    """Discard everything the maker did: tracked edits and untracked files."""
    _git(repo, "reset", "-q", "--", ".")  # undo the intent-to-add
    _git(repo, "restore", ".")            # restore tracked modifications
    _git(repo, "clean", "-fdq")           # remove untracked / new files


def _append_run_log(
    cfg: RunnerConfig,
    *,
    outcome: str,
    items_found: int,
    last_progress: str,
    actions: list[str],
    escalations: Optional[list[str]] = None,
) -> None:
    log = RunLog(cfg.run_log_path or resolve_run_log_path())
    log.append(
        RunLogEntry(
            run_id=f"{cfg.loop_id}-{uuid.uuid4().hex[:8]}",
            loop_id=cfg.loop_id,
            items_found=items_found,
            actions_taken=actions,
            escalations=escalations or [],
            tokens_estimate=0,
            outcome=outcome,
            last_progress=last_progress,
        )
    )


def run_cycle(cfg: RunnerConfig) -> CycleResult:
    """Execute one governed, propose-only cycle."""
    # 1. Governance pre-flight.
    if kill_switch_path(cfg.loop_id).exists() or global_pause_path().exists():
        return CycleResult("paused", "kill-switch or global pause set")

    # 2. Bounded lifetime: expire durably by touching the kill-switch.
    if lifetime.is_expired(cfg.loop_id):
        kill_switch_path(cfg.loop_id).touch()
        _append_run_log(
            cfg,
            outcome="expired",
            items_found=0,
            last_progress="lifetime budget spent; self-stopped",
            actions=["lifetime expired -> touched kill-switch"],
        )
        return CycleResult("expired", "lifetime budget spent; self-stopped")

    # 3. Refuse to run on a dirty tree (revert would clobber uncommitted work).
    pre_existing = _changed_files(cfg.repo)
    if pre_existing:
        _append_run_log(
            cfg,
            outcome="paused",
            items_found=0,
            last_progress="working tree not clean; refusing to run",
            actions=[f"dirty tree: {', '.join(pre_existing[:10])}"],
            escalations=["working tree dirty; a loop needs a clean tree or a dedicated worktree"],
        )
        return CycleResult("paused", "working tree dirty; refusing to run")

    # 4. Next task.
    task = cfg.task_source()
    if not task:
        _append_run_log(
            cfg,
            outcome="no-op",
            items_found=0,
            last_progress="no tasks this cycle",
            actions=[],
        )
        return CycleResult("no-op", "no tasks")

    task_id = str(task.get("id") or task.get("name") or "task")
    started = time.monotonic()
    try:
        summary = cfg.maker(task, cfg.repo)
        files_changed = _changed_files(cfg.repo)
        diff = _diff_text(cfg.repo)

        if not files_changed:
            _append_run_log(
                cfg,
                outcome="no-op",
                items_found=1,
                last_progress=f"{task_id}: maker produced no change",
                actions=[f"maker: {summary}"],
            )
            return CycleResult("no-op", "maker produced no change", task_id=task_id)

        result = cfg.verifier_fn(cfg.repo, task, files_changed, diff)
        verdict = getattr(result.verdict, "value", str(result.verdict))
        reasoning = getattr(result, "reasoning", "")

        escalations: list[str] = []
        if verdict == Verdict.ESCALATE_HUMAN.value:
            outcome = "escalated"
            escalations = [f"{task_id}: verifier ESCALATE_HUMAN - {reasoning[:200]}"]
        else:
            # APPROVE and REJECT are both logged as proposals at L1 (never committed).
            outcome = "fix-proposed"

        _append_run_log(
            cfg,
            outcome=outcome,
            items_found=1,
            last_progress=f"{task_id}: {verdict} (propose-only, not committed)",
            actions=[
                f"maker: {summary}",
                f"verifier: {verdict}",
                f"files: {', '.join(files_changed[:10])}",
            ],
            escalations=escalations,
        )
        return CycleResult(outcome, reasoning, task_id=task_id, verdict=verdict)

    except Exception as exc:  # honest failure — never a silent success
        _append_run_log(
            cfg,
            outcome="error",
            items_found=1,
            last_progress=f"{task_id}: runner error: {exc}",
            actions=[f"error: {exc}"],
            escalations=[f"{task_id}: cycle error {exc}"],
        )
        return CycleResult("error", str(exc), task_id=task_id)

    finally:
        # PROPOSE-ONLY: discard the maker's change on every exit path, and count
        # only the active-work time against the lifetime budget.
        _revert_tree(cfg.repo)
        lifetime.tick(cfg.loop_id, int(time.monotonic() - started))


def run(cfg: RunnerConfig) -> list[CycleResult]:
    """Run cycles until governance stops the loop (paused / expired) or max_cycles."""
    results: list[CycleResult] = []
    cycle = 0
    while cfg.max_cycles is None or cycle < cfg.max_cycles:
        result = run_cycle(cfg)
        results.append(result)
        cycle += 1
        if result.outcome in ("paused", "expired"):
            break
        if cfg.poll_interval_seconds > 0:
            time.sleep(cfg.poll_interval_seconds)
    return results


# --- default building blocks a CLI / skill / MCP can wire together -----------

def file_task_source(path: Path) -> TaskSource:
    """Task source backed by a JSON list file; pops the first task each call.

    The file holds a JSON array of task dicts. Each call returns and removes the
    first element (atomic tmp+rename write), or None when empty/missing.
    """
    path = Path(path)

    def _next() -> Optional[dict]:
        if not path.exists():
            return None
        try:
            tasks = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(tasks, list) or not tasks:
            return None
        task = tasks.pop(0)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return task if isinstance(task, dict) else None

    return _next


def _build_prompt(task: dict) -> str:
    name = task.get("name") or task.get("id") or "task"
    instruction = task.get("instruction") or ""
    return (
        f"Task: {name}\n\n{instruction}\n\n"
        "Make the change in the working tree. Do NOT commit, do NOT push, do NOT "
        "amend git history. Keep the diff as small as possible and scoped to this task."
    )


def cursor_agent_maker(model: str = "composer-2.5", *, timeout: int = 900) -> Maker:
    """Default maker: shell out to cursor-agent (sanctioned, QB-routed)."""

    def _maker(task: dict, repo: Path) -> str:
        proc = subprocess.run(
            ["cursor-agent", "-p", "--yolo", "--trust", "--model", model, _build_prompt(task)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or proc.stderr or "cursor-agent produced no output").strip()
        return out[:500]

    return _maker
