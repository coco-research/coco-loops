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

Maker failure vs. genuine no-op: a maker that legitimately ran and decided there was
nothing to change is a completely different outcome from a maker that could not run
at all (missing binary, crashed, or failed to authenticate). CLI-based makers built
with `cli_agent_maker` (including the default `cursor_agent_maker`) never swallow a
non-zero exit into a stringified "summary" — they raise `MakerAuthError` / `MakerError`
carrying the real stderr, so `run_cycle` can log a distinct `blocked` / `maker-error`
outcome instead of a bare `no-op`. A cheap `maker_preflight` probe can also be
configured on `RunnerConfig` to refuse to start before the first cycle (see
`maker_preflight_gate`, `cursor_agent_preflight`), so a loop does not spend its
lifetime budget on cycles that cannot possibly succeed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from coco_loops import lifetime
from coco_loops.paths import global_pause_path, kill_switch_path, resolve_run_log_path
from coco_loops.run_log import RunLog, RunLogEntry
from coco_loops.verifier import Verdict, verify_proposal

# A maker edits the working tree to propose a change for one task, and returns a
# short human-readable summary. The runner does NOT trust it: the diff is read
# from git afterwards. A maker that could not run at all MUST raise MakerError (or
# MakerAuthError) rather than returning a stringified failure — that is what lets
# run_cycle tell "ran and found nothing" apart from "never actually ran".
Maker = Callable[[dict, Path], str]

# A task source returns the next task dict (with at least an 'id' plus a 'name' or
# 'instruction'), or None when there is nothing to do this cycle.
TaskSource = Callable[[], Optional[dict]]

# A verifier judges (repo, task, files_changed, diff_text) and returns an object
# exposing `.verdict` and `.reasoning`. Defaults to coco_loops.verifier.verify_proposal.
VerifierFn = Callable[[Path, dict, list, str], object]


class MakerError(RuntimeError):
    """The maker CLI ran but failed: non-zero exit, crash, or missing binary.

    Distinct from a genuine no-op (the maker ran fine and legitimately found nothing
    to change — files_changed stays empty) and from a runner-level bug (caught
    generically by run_cycle as outcome="error"). Carries the real stderr/stdout so
    the run-log shows an operator what actually happened instead of a bare "no-op".
    """

    def __init__(self, message: str, *, returncode: Optional[int] = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class MakerAuthError(MakerError):
    """The maker CLI could not authenticate (login lapsed / no API key configured).

    A more specific, more actionable case of MakerError: the fix is an operator
    action (e.g. `cursor-agent login`), not a code or task-quality problem.
    """


@dataclass
class PreflightResult:
    """Result of a cheap probe: can the maker actually run right now?"""

    ok: bool
    detail: str
    hint: str = ""


# A pre-flight probe, run once before the first cycle. Returns a PreflightResult;
# never raises (a probe that cannot tell should report ok=False with a clear detail
# rather than let an exception escape and be mistaken for something else).
MakerPreflight = Callable[[], PreflightResult]

# Default: pause the loop after this many consecutive maker-error/blocked outcomes,
# rather than grinding through the rest of the lifetime budget on a maker that
# cannot succeed. None disables the breaker.
DEFAULT_MAKER_ERROR_CIRCUIT_BREAKER = 3


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
    maker_preflight: Optional[MakerPreflight] = None
    maker_error_circuit_breaker: Optional[int] = DEFAULT_MAKER_ERROR_CIRCUIT_BREAKER
    # Called with the task dict when a cycle ends without judging it, so the task goes
    # back to the queue instead of being silently spent. Without this, running with a
    # broken maker drains the whole queue while doing no work, and an operator who then
    # fixes the maker restarts against an empty queue and concludes the work is done.
    requeue: Optional[Callable[[dict], None]] = None
    # Where to persist each cycle's proposed diff. A propose-only loop whose proposals are
    # discarded has no output at all, so this defaults to being written next to the run-log.
    proposals_dir: Optional[Path] = None


@dataclass
class CycleResult:
    # paused | expired | no-op | fix-proposed | escalated | error | maker-error | blocked
    outcome: str
    detail: str = ""
    task_id: Optional[str] = None
    verdict: Optional[str] = None
    # Path to the saved diff, when the cycle produced one. Lets a caller review the batch
    # of proposals without re-reading the run-log.
    proposal_path: Optional[str] = None


def _requeue(cfg: "RunnerConfig", task: Optional[dict], outcome: str) -> None:
    """Return an unjudged task to the queue.

    A cycle that ended in blocked, maker-error or error never got a verdict on the task,
    so consuming it would lose real work silently. Requeue failures are swallowed on
    purpose: the cycle has already failed and its outcome is already in the run-log, so
    raising here would replace an honest failure with a confusing one.
    """
    if task is None or cfg.requeue is None:
        return
    try:
        cfg.requeue(task)
    except Exception:
        pass


def _save_proposal(cfg: "RunnerConfig", task_id: str, diff: str, verdict: str) -> Optional[Path]:
    """Persist a cycle's diff so a propose-only run leaves something to review.

    The tree is reverted after every cycle, so a diff held only in memory is gone. That
    made REJECT verdicts produce no artifact at all, which defeats the point of a
    propose-only loop: there was no batch of proposals for the human to review. Written
    for every verdict, because a rejected proposal is often still informative.
    """
    if not diff:
        return None
    base = cfg.proposals_dir
    if base is None:
        anchor = cfg.run_log_path.parent if cfg.run_log_path else cfg.repo
        base = Path(anchor) / "proposals"
    try:
        base = Path(base)
        base.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", task_id)[:60] or "task"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = base / f"{stamp}-{safe}-{verdict.lower()}.patch"
        out.write_text(diff, encoding="utf-8")
        return out
    except Exception:
        # Never let artifact bookkeeping turn a real proposal into a cycle failure.
        return None


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

        # Persist the diff BEFORE the finally-block revert throws the working tree away.
        # This is the only durable output of a propose-only cycle.
        saved = _save_proposal(cfg, task_id, diff, verdict)

        actions = [
            f"maker: {summary}",
            f"verifier: {verdict}",
            f"files: {', '.join(files_changed[:10])}",
        ]
        if saved:
            actions.append(f"proposal saved: {saved}")

        _append_run_log(
            cfg,
            outcome=outcome,
            items_found=1,
            last_progress=f"{task_id}: {verdict} (propose-only, not committed)",
            actions=actions,
            escalations=escalations,
        )
        return CycleResult(
            outcome, reasoning, task_id=task_id, verdict=verdict,
            proposal_path=str(saved) if saved else None,
        )

    except MakerAuthError as exc:
        # The maker did not actually run: it could not authenticate. This is NOT a
        # no-op (the maker never got to decide anything) and NOT a generic runner
        # error (it is an operator-actionable, named failure mode) — it gets its own
        # outcome, "blocked", with the real stderr carried into the run-log.
        stderr = (exc.stderr or str(exc)).strip()[:2000]
        _append_run_log(
            cfg,
            outcome="blocked",
            items_found=1,
            last_progress=f"{task_id}: maker could not authenticate - {stderr[:200]}",
            actions=[f"maker auth error: {stderr}"],
            escalations=[
                f"{task_id}: maker cannot authenticate — operator must re-authenticate "
                f"the maker before this loop can do anything. Detail: {stderr[:300]}"
            ],
        )
        _requeue(cfg, task, "blocked")
        return CycleResult("blocked", stderr, task_id=task_id)

    except MakerError as exc:
        # The maker ran but failed (non-zero exit, crash, missing binary) — never
        # record this as a no-op. Distinct outcome, real stderr in the run-log.
        stderr = (exc.stderr or str(exc)).strip()[:2000]
        _append_run_log(
            cfg,
            outcome="maker-error",
            items_found=1,
            last_progress=f"{task_id}: maker failed to run - {stderr[:200]}",
            actions=[f"maker error: {stderr}"],
            escalations=[f"{task_id}: maker failed to run - {stderr[:300]}"],
        )
        _requeue(cfg, task, "maker-error")
        return CycleResult("maker-error", stderr, task_id=task_id)

    except Exception as exc:  # honest failure — never a silent success
        _append_run_log(
            cfg,
            outcome="error",
            items_found=1,
            last_progress=f"{task_id}: runner error: {exc}",
            actions=[f"error: {exc}"],
            escalations=[f"{task_id}: cycle error {exc}"],
        )
        _requeue(cfg, task, "error")
        return CycleResult("error", str(exc), task_id=task_id)

    finally:
        # PROPOSE-ONLY: discard the maker's change on every exit path, and count
        # only the active-work time against the lifetime budget.
        _revert_tree(cfg.repo)
        lifetime.tick(cfg.loop_id, int(time.monotonic() - started))


def maker_preflight_gate(cfg: RunnerConfig) -> Optional[CycleResult]:
    """Run the configured maker pre-flight once, before any cycle runs.

    Returns None when there is no pre-flight configured, or it passed — the caller
    should proceed. Returns an already-logged ``blocked`` CycleResult when it failed,
    so a loop refuses to start rather than spending its lifetime budget (and popping
    tasks off the queue) on cycles that cannot possibly succeed. Deliberately does
    NOT touch the lifetime counter and does NOT call the task source.
    """
    if cfg.maker_preflight is None:
        return None
    result = cfg.maker_preflight()
    if result.ok:
        return None
    hint = f" {result.hint}" if result.hint else ""
    detail = f"{result.detail}.{hint}".strip()
    _append_run_log(
        cfg,
        outcome="blocked",
        items_found=0,
        last_progress=f"maker pre-flight failed: {result.detail}",
        actions=[f"pre-flight: {result.detail}"],
        escalations=[f"maker cannot run - {detail}"],
    )
    return CycleResult("blocked", detail)


def _is_maker_failure(outcome: str) -> bool:
    return outcome in ("maker-error", "blocked")


def run(cfg: RunnerConfig) -> list[CycleResult]:
    """Run cycles until governance stops the loop (paused / expired) or max_cycles.

    Pre-flights the maker once before the first cycle (see `maker_preflight_gate`),
    and acts as a circuit breaker: `cfg.maker_error_circuit_breaker` consecutive
    maker-error/blocked outcomes self-pause the loop (touch the kill-switch) instead
    of grinding through the rest of the lifetime budget on a maker that cannot run.
    """
    gate = maker_preflight_gate(cfg)
    if gate is not None:
        return [gate]

    results: list[CycleResult] = []
    cycle = 0
    consecutive_maker_failures = 0
    while cfg.max_cycles is None or cycle < cfg.max_cycles:
        result = run_cycle(cfg)
        results.append(result)
        cycle += 1

        if _is_maker_failure(result.outcome):
            consecutive_maker_failures += 1
        else:
            consecutive_maker_failures = 0

        if result.outcome in ("paused", "expired"):
            break

        if (
            cfg.maker_error_circuit_breaker is not None
            and consecutive_maker_failures >= cfg.maker_error_circuit_breaker
        ):
            kill_switch_path(cfg.loop_id).touch()
            breaker_detail = (
                f"circuit breaker: {consecutive_maker_failures} consecutive maker "
                "failures; self-paused"
            )
            _append_run_log(
                cfg,
                outcome="paused",
                items_found=0,
                last_progress=breaker_detail,
                actions=[
                    f"circuit breaker tripped after {consecutive_maker_failures} "
                    "consecutive maker-error/blocked outcomes"
                ],
                escalations=[
                    "maker failed repeatedly; loop self-paused (kill-switch set). "
                    "Check the maker (auth, PATH, model) before clearing it."
                ],
            )
            # Record the trip as its own result so a caller inspecting results[-1]
            # (e.g. the CLI's multi-cycle summary) sees "paused", not a stale
            # maker-error, as the reason the loop stopped.
            results.append(CycleResult("paused", breaker_detail))
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

    def _requeue_task(task: dict) -> None:
        """Put an unjudged task back at the head of the queue.

        Wire this to RunnerConfig.requeue. Without it, a cycle that never reached a
        verdict still spends the task, so a run against a broken maker empties the queue
        having done nothing.
        """
        try:
            tasks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (json.JSONDecodeError, OSError):
            tasks = []
        if not isinstance(tasks, list):
            tasks = []
        ident = task.get("id")
        # Idempotent: never duplicate a task that is somehow already back in the queue.
        if ident is not None and any(
            isinstance(t, dict) and t.get("id") == ident for t in tasks
        ):
            return
        tasks.insert(0, task)
        tmp = path.with_suffix(path.suffix + ".requeue.tmp")
        tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # Attached rather than returned as a tuple so existing callers keep working.
    _next.requeue = _requeue_task  # type: ignore[attr-defined]
    return _next


def _build_prompt(task: dict) -> str:
    name = task.get("name") or task.get("id") or "task"
    instruction = task.get("instruction") or ""
    return (
        f"Task: {name}\n\n{instruction}\n\n"
        "Make the change in the working tree. Do NOT commit, do NOT push, do NOT "
        "amend git history. Keep the diff as small as possible and scoped to this task."
    )


# Phrases that show up in a CLI agent's stderr/stdout when it could not authenticate.
# Deliberately textual and specific (never a bare status code) to keep false positives
# low: matching too eagerly would mislabel a real crash as an auth problem.
_AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "authentication required",
    "not logged in",
    "agent login",
    "cursor_api_key",
    "api key is invalid",
    "invalid api key",
    "unauthorized",
    "please authenticate",
    "please log in",
    "please run 'login'",
)


def _looks_like_auth_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _AUTH_ERROR_MARKERS)


def cli_agent_maker(
    argv_prefix: list[str],
    *,
    timeout: int = 900,
) -> Maker:
    """Build a Maker from any CLI tool shaped like ``<cmd> [flags...] <prompt>``.

    This is the pluggable seam: a maker is "given a task instruction and a repo,
    produce a diff", and a large class of agentic CLIs (cursor-agent, and others with
    the same "flags then a trailing prompt string, edits the cwd, prints a summary"
    shape) fit it directly. `cursor_agent_maker` below is just the default instance of
    this shape; a consumer can configure a different CLI-based maker by calling this
    directly (or via `coco-loops run --maker-cmd "..."`) and passing the result as
    `RunnerConfig.maker`, with no change to the runner's governance logic.

    Never swallows a failure into a stringified "summary" the way the old cursor-agent
    maker did: a missing binary, non-zero exit, or timeout raises `MakerError` (or the
    more specific `MakerAuthError` when the output looks like an auth failure) so
    `run_cycle` can log a distinct outcome instead of a false "no-op".
    """

    def _maker(task: dict, repo: Path) -> str:
        argv = [*argv_prefix, _build_prompt(task)]
        try:
            proc = subprocess.run(
                argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise MakerError(
                f"maker binary not found on PATH: {argv[0]!r} ({exc})", stderr=str(exc)
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MakerError(
                f"maker timed out after {timeout}s running: {' '.join(argv[:-1])}",
                stderr=str(exc),
            ) from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            message = f"{argv[0]} exited {proc.returncode}: {stderr[:500] or '(no output)'}"
            if _looks_like_auth_error(stderr) or _looks_like_auth_error(proc.stdout or ""):
                raise MakerAuthError(message, returncode=proc.returncode, stderr=stderr)
            raise MakerError(message, returncode=proc.returncode, stderr=stderr)

        out = (proc.stdout or proc.stderr or f"{argv[0]} produced no output").strip()
        return out[:500]

    return _maker


def cursor_agent_maker(model: str = "composer-2.5", *, timeout: int = 900) -> Maker:
    """Default maker: shell out to cursor-agent (sanctioned, QB-routed).

    A thin specialization of `cli_agent_maker` for cursor-agent's flags; behavior on
    success is unchanged from before. On failure it now raises instead of returning
    the error text as if it were a legitimate maker summary.
    """
    return cli_agent_maker(
        ["cursor-agent", "-p", "--yolo", "--trust", "--model", model], timeout=timeout
    )


_CURSOR_AGENT_LOGIN_HINT = (
    "Fix: run `cursor-agent login` (or set CURSOR_API_KEY), then re-arm the loop."
)


def generic_binary_preflight(binary: str) -> PreflightResult:
    """Cheapest possible probe for an arbitrary CLI-based maker: is it on PATH?

    Used for a custom `--maker-cmd` maker, where the framework has no way to know
    that CLI's specific status/auth subcommand. This is intentionally weaker than
    `cursor_agent_preflight` (PATH presence only, no auth check) — see the module
    docstring: a full generic auth probe was judged too tool-specific to build safely
    for an unknown CLI, so this checks the one thing that generalizes.
    """
    if shutil.which(binary) is None:
        return PreflightResult(
            ok=False,
            detail=f"maker binary not found on PATH: {binary!r}",
            hint="Install it or fix PATH, then re-arm the loop.",
        )
    return PreflightResult(ok=True, detail=f"{binary} found on PATH (auth not probed)")


def cursor_agent_preflight(*, timeout: int = 15) -> PreflightResult:
    """Cheapest possible probe: is cursor-agent on PATH and authenticated?

    Uses ``cursor-agent status --format json`` — a lightweight status/whoami call,
    not a real generation request — so a lapsed login is caught before any lifetime
    budget or task-queue entry is spent on cycles that cannot succeed.
    """
    try:
        proc = subprocess.run(
            ["cursor-agent", "status", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return PreflightResult(
            ok=False,
            detail="cursor-agent is not on PATH",
            hint="Install cursor-agent and ensure it is on PATH.",
        )
    except subprocess.TimeoutExpired:
        return PreflightResult(
            ok=False,
            detail=f"cursor-agent status did not respond within {timeout}s",
            hint="Check the cursor-agent installation/network and retry.",
        )

    raw = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        return PreflightResult(
            ok=False,
            detail=f"cursor-agent status exited {proc.returncode}: {raw[:300]}",
            hint=_CURSOR_AGENT_LOGIN_HINT,
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Unexpected output shape from a future cursor-agent version: fall back to a
        # text scan rather than blocking a loop on a parsing quirk.
        if _looks_like_auth_error(raw):
            return PreflightResult(ok=False, detail=raw[:300], hint=_CURSOR_AGENT_LOGIN_HINT)
        return PreflightResult(
            ok=True, detail="cursor-agent status: unparsed output, no auth-error text found"
        )

    if payload.get("isAuthenticated") is False:
        return PreflightResult(
            ok=False,
            detail=str(payload.get("message") or "cursor-agent is not authenticated"),
            hint=_CURSOR_AGENT_LOGIN_HINT,
        )

    return PreflightResult(
        ok=True, detail=str(payload.get("message") or "cursor-agent authenticated")
    )
