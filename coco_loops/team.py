"""Hire a team and delegate subtasks across it, under one governed loop.

Generic and standalone: a running loop can decompose a task into subtasks and fan
them across several makers ("hire a team"). Each subtask is executed as a full
governed, propose-only cycle via the runner, so the propose-only guarantee (capture
the diff, verify, log, revert) holds for every subtask, and any governance stop
(kill-switch, lifetime expiry, dirty tree) halts delegation immediately.

Subtasks run sequentially in one worktree: each is fully proposed, verified, logged,
and reverted before the next begins, so sub-makers never clobber each other. Parallel
delegation would need a worktree per member; that is a later concern.

No CoCo coupling. A "team member" is a role paired with a maker. The default team
builds one cursor-agent maker per role; a consumer may supply specialized makers
(different models or prompts per role) instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from coco_loops.runner import (
    CycleResult,
    Maker,
    RunnerConfig,
    VerifierFn,
    cursor_agent_maker,
    run_cycle,
)
from coco_loops.verifier import verify_proposal


@dataclass
class Subtask:
    id: str
    instruction: str
    role: str = "default"


@dataclass
class Team:
    members: dict[str, Maker]

    def maker_for(self, role: str) -> Maker:
        if role in self.members:
            return self.members[role]
        if "default" in self.members:
            return self.members["default"]
        if not self.members:
            raise ValueError("team has no members")
        return next(iter(self.members.values()))

    @property
    def roles(self) -> list[str]:
        return list(self.members.keys())


def hire(roles: Iterable[str], *, model: str = "composer-2.5") -> Team:
    """Build a team with one cursor-agent maker per role (the default hiring)."""
    role_list = list(roles) or ["default"]
    return Team(members={role: cursor_agent_maker(model) for role in role_list})


def _subtask_to_task(parent_task: dict, sub: Subtask) -> dict:
    parent_id = str(parent_task.get("id") or parent_task.get("name") or "task")
    parent_goal = parent_task.get("objective") or parent_task.get("name") or ""
    instruction = sub.instruction
    if parent_goal:
        instruction = f"Parent goal: {parent_goal}\nSubtask: {sub.instruction}"
    return {
        "id": f"{parent_id}:{sub.id}",
        "name": sub.instruction[:80],
        "instruction": instruction,
    }


def delegate(
    parent_task: dict,
    subtasks: Iterable[Subtask],
    *,
    loop_id: str,
    repo: Path,
    team: Team,
    verifier_fn: VerifierFn = verify_proposal,
    run_log_path: Optional[Path] = None,
) -> list[CycleResult]:
    """Run each subtask as a governed propose-only cycle via a role-routed maker.

    Returns one CycleResult per subtask attempted. Stops early (and does not attempt
    the remaining subtasks) if a governance gate pauses or expires the loop.
    """
    results: list[CycleResult] = []
    for sub in subtasks:
        task = _subtask_to_task(parent_task, sub)
        maker = team.maker_for(sub.role)
        cfg = RunnerConfig(
            loop_id=loop_id,
            repo=repo,
            task_source=(lambda t=task: t),  # run_cycle pulls one task
            maker=maker,
            verifier_fn=verifier_fn,
            run_log_path=run_log_path,
            poll_interval_seconds=0,
        )
        result = run_cycle(cfg)
        results.append(result)
        if result.outcome in ("paused", "expired"):
            break
    return results
