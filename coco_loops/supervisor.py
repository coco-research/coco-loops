"""Heartbeat supervisor — third control signal beside kill-switch and budget-pause."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Protocol

from coco_loops.run_log import RunLog, RunLogEntry


class Liveness(str, Enum):
    ALIVE = "ALIVE"
    DEAD = "DEAD"
    DOZING = "DOZING"


class SupervisorAction(str, Enum):
    NONE = "NONE"
    REVIVE = "REVIVE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


@dataclass(frozen=True)
class SupervisorConfig:
    cadence_seconds: int
    tolerance_intervals: int = 3
    max_revives: int = 3


@dataclass
class SupervisorState:
    revive_count: int = 0


@dataclass
class SupervisorDecision:
    liveness: Liveness
    action: SupervisorAction
    evidence: list[str] = field(default_factory=list)


class ProcessChecker(Protocol):
    def __call__(self) -> bool: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...


def _parse_progress_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_liveness(
    *,
    last_progress: str | None,
    now: datetime,
    cadence_seconds: int,
    tolerance_intervals: int,
    process_alive: bool,
) -> Liveness:
    if not process_alive:
        return Liveness.DEAD

    progress_ts = _parse_progress_ts(last_progress)
    if progress_ts is None:
        return Liveness.DOZING

    elapsed = (now - progress_ts).total_seconds()
    tolerance = cadence_seconds * tolerance_intervals
    if elapsed > tolerance:
        return Liveness.DOZING
    return Liveness.ALIVE


def decide(
    *,
    config: SupervisorConfig,
    state: SupervisorState,
    run_log: RunLog,
    process_checker: ProcessChecker,
    clock: Clock | None = None,
) -> SupervisorDecision:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    last_progress = run_log.last_progress()
    alive = process_checker()
    liveness = classify_liveness(
        last_progress=last_progress,
        now=now,
        cadence_seconds=config.cadence_seconds,
        tolerance_intervals=config.tolerance_intervals,
        process_alive=alive,
    )

    evidence = [
        f"process_alive={alive}",
        f"last_progress={last_progress!r}",
        f"revive_count={state.revive_count}/{config.max_revives}",
    ]

    if liveness == Liveness.ALIVE:
        return SupervisorDecision(liveness=liveness, action=SupervisorAction.NONE, evidence=evidence)

    if liveness == Liveness.DEAD:
        if state.revive_count < config.max_revives:
            evidence.append("Process missing — scheduling revive.")
            return SupervisorDecision(
                liveness=liveness, action=SupervisorAction.REVIVE, evidence=evidence
            )
        evidence.append("Process missing and revive budget exhausted — escalate.")
        return SupervisorDecision(
            liveness=liveness,
            action=SupervisorAction.ESCALATE_HUMAN,
            evidence=evidence,
        )

    # DOZING
    if state.revive_count < config.max_revives:
        evidence.append("Process alive but stale progress — scheduling revive.")
        return SupervisorDecision(
            liveness=liveness, action=SupervisorAction.REVIVE, evidence=evidence
        )
    evidence.append("Dozing past tolerance and revive budget exhausted — escalate.")
    return SupervisorDecision(
        liveness=liveness,
        action=SupervisorAction.ESCALATE_HUMAN,
        evidence=evidence,
    )


def apply_revive(state: SupervisorState) -> SupervisorState:
    return SupervisorState(revive_count=state.revive_count + 1)


# --- live driver: turn the pure decide() into a running heartbeat watcher -------

@dataclass
class WatchConfig:
    loop_id: str
    supervisor: SupervisorConfig
    process_checker: ProcessChecker
    revive_fn: Optional[Callable[[], None]] = None
    once: bool = False
    max_iterations: Optional[int] = None


def _log_supervisor(run_log: RunLog, loop_id: str, decision: SupervisorDecision, tag: str) -> None:
    escalations = (
        [f"supervisor escalation: {decision.liveness.value}"]
        if decision.action == SupervisorAction.ESCALATE_HUMAN
        else []
    )
    run_log.append(
        RunLogEntry(
            run_id=f"{loop_id}-sup-{uuid.uuid4().hex[:8]}",
            loop_id=loop_id,
            items_found=0,
            actions_taken=[
                f"supervisor: {decision.liveness.value} -> {decision.action.value} ({tag})",
                *decision.evidence,
            ],
            escalations=escalations,
            tokens_estimate=0,
            outcome=f"supervisor-{tag}",
            last_progress=f"supervisor {tag}: {decision.liveness.value}",
        )
    )


def watch(
    config: WatchConfig,
    run_log: RunLog,
    *,
    clock: Clock | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> list[SupervisorDecision]:
    """Run the heartbeat: classify liveness, act (revive / escalate), repeat.

    REVIVE calls the injected revive_fn (e.g. clear the kill-switch or kickstart the
    daemon) and increments the revive count. ESCALATE_HUMAN logs and stops, because a
    human must intervene. NONE (alive) just records nothing new. The watcher stops on
    the first escalation, when `once` is set, or after `max_iterations`.
    """
    sleep = sleep_fn or time.sleep
    state = SupervisorState()
    decisions: list[SupervisorDecision] = []
    iteration = 0

    while True:
        decision = decide(
            config=config.supervisor,
            state=state,
            run_log=run_log,
            process_checker=config.process_checker,
            clock=clock,
        )
        decisions.append(decision)

        if decision.action == SupervisorAction.REVIVE:
            if config.revive_fn is not None:
                config.revive_fn()
            state = apply_revive(state)
            _log_supervisor(run_log, config.loop_id, decision, "revive")
        elif decision.action == SupervisorAction.ESCALATE_HUMAN:
            _log_supervisor(run_log, config.loop_id, decision, "escalate")
            break

        iteration += 1
        if config.once or (config.max_iterations is not None and iteration >= config.max_iterations):
            break
        sleep(config.supervisor.cadence_seconds)

    return decisions
