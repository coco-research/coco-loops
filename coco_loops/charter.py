"""Charter: turn a human goal into a governed loop contract.

The charter is the bridge between a vague, natural-language goal ("keep my build
green for the next four hours") and a governed loop. The intelligence that turns
that sentence into a structured charter lives in the CONSUMER (the /coco-loop
skill in Claude Code, or an MCP client in Cursor) using whatever model it has.

This module is the framework side, and it is deliberately dumb and pure: it takes
an already-compiled charter (a plain dict / dataclass), validates it against the
immutable constitution floor, renders it for human confirmation, and installs it
(writes the contract file and arms the lifetime cap). Stdlib only, no LLM, no
network, so the governance layer stays auditable and testable.

Flow:
    consumer LLM      -> compiles goal into a Charter dict
    charter.validate  -> rejects anything that contradicts the constitution
    charter.render    -> human-readable text the user confirms
    charter.install   -> writes <loop_id>.contract.md + arms the lifetime cap
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from coco_loops import lifetime
from coco_loops.paths import (
    kill_switch_path,
    resolve_loops_dir,
    resolve_state_dir,
)

CONTRACT_SUFFIX = ".contract.md"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Only L1 (report-only / propose-only) may be declared when a loop is created.
# Higher maturity is EARNED through the promotion gate, never self-declared by a
# charter (constitution I7, I8).
ALLOWED_INITIAL_LEVELS = {"L1"}

# Red-flag patterns. If a charter's objective / principles / guardrails text asks
# for any of these, the charter is trying to contract around the immutable floor.
# Each maps to the invariant it would violate (see specs/loop-constitution.md).
_FLOOR_VIOLATIONS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"(disable|bypass|skip|no-?op|turn\s*off|remove)\s+(the\s+)?verif", re.I),
        "I1",
        "would disable or bypass the verifier",
    ),
    (
        re.compile(
            r"auto-?merge|commit\s+without\s+(review|approv|the\s+verifier)"
            r"|self-?approv|skip\s+(review|the\s+verifier)",
            re.I,
        ),
        "I4/I8",
        "would commit without verifier approval",
    ),
    (
        re.compile(r"(amend|edit|rewrite|modify|disable|change)\s+(the\s+)?(constitution|denylist)", re.I),
        "I2/I6",
        "would amend the constitution or denylist",
    ),
    (
        re.compile(r"api\.openai\.com|api\.anthropic\.com|raw\s+public\s+cloud", re.I),
        "I3",
        "would send content to unsanctioned public-cloud endpoints",
    ),
    (
        re.compile(
            r"git\s+push|force[- ]?push|bulk\s+(purge|delete)|drop\s+table"
            r"|truncate\s+table|migration\s+apply",
            re.I,
        ),
        "I5",
        "would perform an unattended destructive or push operation",
    ),
]

# The shape a consumer must produce. Printed by `coco-loops charter --schema` so a
# skill / MCP client knows exactly what to fill in.
CHARTER_SCHEMA: dict = {
    "loop_id": "slug, e.g. keep-build-green (lowercase, digits, hyphens)",
    "objective": "one sentence: the bounded improvement the loop pursues each cycle",
    "principles": ["readable rule the loop must follow", "...at least one"],
    "guardrails": ["hard line the loop must never cross (optional)"],
    "context": ["where the loop reads operational state (optional)"],
    "inputs": ["queue / watchlist the loop reads each cycle (optional)"],
    "lifetime": "active-work cap, e.g. 90s, 45m, 6h, 1h30m (default 4h)",
    "level": "L1 only at creation (higher maturity is earned via the promotion gate)",
    "budget_tokens_day": "optional int override",
    "budget_runs_day": "optional int override",
}


class CharterError(ValueError):
    """Raised when a charter is malformed or violates the constitution floor."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _strlist(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _optint(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


@dataclass
class Charter:
    loop_id: str
    objective: str
    principles: list[str] = field(default_factory=list)
    guardrails: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    lifetime: str = "4h"
    level: str = "L1"
    budget_tokens_day: int | None = None
    budget_runs_day: int | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Charter":
        if not isinstance(data, dict):
            raise CharterError(["charter must be a JSON object"])
        return cls(
            loop_id=str(data.get("loop_id", "")).strip(),
            objective=str(data.get("objective", "")).strip(),
            principles=_strlist(data.get("principles")),
            guardrails=_strlist(data.get("guardrails")),
            context=_strlist(data.get("context")),
            inputs=_strlist(data.get("inputs")),
            lifetime=(str(data.get("lifetime", "4h")).strip() or "4h"),
            level=(str(data.get("level", "L1")).strip() or "L1"),
            budget_tokens_day=_optint(data.get("budget_tokens_day")),
            budget_runs_day=_optint(data.get("budget_runs_day")),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def validate(charter: Charter) -> list[str]:
    """Return a list of problems; empty means the charter is valid.

    Enforces the parts of the constitution floor a charter could try to contract
    around: it may not self-declare maturity above L1, and its text may not ask
    for anything that violates invariants I1-I8.
    """
    problems: list[str] = []

    if not _SLUG_RE.match(charter.loop_id or ""):
        problems.append(
            f"loop_id {charter.loop_id!r} must be a slug: lowercase letters, digits, "
            "hyphens, 1-64 chars"
        )
    if not (charter.objective or "").strip():
        problems.append("objective is required (one sentence describing the goal)")
    if not charter.principles:
        problems.append("at least one principle is required (the readable rules the loop obeys)")

    if charter.level not in ALLOWED_INITIAL_LEVELS:
        problems.append(
            f"level {charter.level!r} is not allowed at creation; new loops MUST start L1 "
            "(propose-only). Higher maturity is earned through the promotion gate [I7/I8]"
        )

    try:
        lifetime.parse_duration(charter.lifetime)
    except ValueError:
        problems.append(
            f"lifetime {charter.lifetime!r} is not a valid duration (e.g. 90s, 45m, 6h, 1h30m)"
        )

    blob = "\n".join([charter.objective, *charter.principles, *charter.guardrails])
    for pattern, invariant, description in _FLOOR_VIOLATIONS:
        if pattern.search(blob):
            problems.append(
                f"charter text {description} - forbidden by constitution {invariant}"
            )

    if charter.budget_tokens_day is not None and charter.budget_tokens_day <= 0:
        problems.append("budget_tokens_day must be a positive integer")
    if charter.budget_runs_day is not None and charter.budget_runs_day <= 0:
        problems.append("budget_runs_day must be a positive integer")

    return problems


def render(charter: Charter) -> str:
    """Human-readable charter for confirmation before the loop is armed."""
    title = f"Loop charter: {charter.loop_id}"
    lines = [title, "=" * len(title), ""]
    lines.append(f"Goal:     {charter.objective}")
    lines.append(
        f"Runs at:  {charter.level} (propose-only - the loop suggests fixes and never "
        "commits; you review the batch)"
    )
    lines.append(
        f"Lifetime: up to {charter.lifetime} of active work, then it stops itself. "
        "Laptop asleep or idle does not burn the clock."
    )
    lines.append("")
    lines.append("Principles it will follow:")
    for principle in charter.principles:
        lines.append(f"  - {principle}")
    if charter.guardrails:
        lines.append("")
        lines.append("Hard guardrails (never crosses):")
        for guardrail in charter.guardrails:
            lines.append(f"  - {guardrail}")
    if charter.context:
        lines.append("")
        lines.append("Reads context from:")
        for source in charter.context:
            lines.append(f"  - {source}")
    if charter.inputs:
        lines.append("")
        lines.append("Inputs each cycle:")
        for item in charter.inputs:
            lines.append(f"  - {item}")
    lines.append("")
    lines.append(
        "Floor (cannot be overridden by this charter): inherits the immutable "
        "constitution - the verifier stays on, nothing commits without approval, no "
        "unsanctioned egress, and destructive or push operations stay attended."
    )
    return "\n".join(lines)


def to_contract_markdown(charter: Charter) -> str:
    """Render the charter as a <loop_id>.contract.md that the CLI can discover.

    Matches specs/loop-contract.template.md closely enough that `coco-loops list`
    infers the level and the verifier can read the inherited floor.
    """
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- (none specified)"

    budget_note = "Framework defaults (see loop-budget.md)."
    if charter.budget_tokens_day or charter.budget_runs_day:
        budget_note = (
            f"tokens/day: {charter.budget_tokens_day or 'default'}, "
            f"runs/day: {charter.budget_runs_day or 'default'}."
        )

    return f"""# {charter.loop_id} Agentic Contract

**Registry entry for `{charter.loop_id}`** - a consumer loop compiled from a human goal via `coco-loops charter`.

Inherits: loop-constitution.md (Layer 0, immutable)

**Anatomy:** Kalra & Mora, *Agentic Contracts* (2026) - `C = ⟨O, K, I, Y, Φ, A, Π, E⟩`.

**Phase P1 maturity: {charter.level} report-only** - propose fixes with a logged verifier dry-run; do not auto-commit until the promotion gate PASSes.

---

## O - Objective

{charter.objective}

One bounded improvement per cycle. L1 = report-only.

## K - Context

{bullets(charter.context)}

## I - Inputs

{bullets(charter.inputs)}

Pre-flight each cycle: load loop-constraints.md; emit `constraints loaded: N rules`.

## Y - Outputs

Proposals in the run-log. No auto-commit at L1.

## Φ - Constraints (this loop's principles, on top of the immutable floor)

Principles:
{bullets(charter.principles)}

Guardrails:
{bullets(charter.guardrails)}

Inherits constitution I1-I8. Loaded pre-flight after the kill-switch check.

## A - Acceptance (verifier - NOT maker)

Separate adversary (`coco_loops.verifier`), default REJECT. Runs tests itself.
Maker must not self-approve (I8).

## Π - Permissions and budget

{budget_note} Kill switch: `<state-dir>/{charter.loop_id}.disabled`.
Lifetime cap: {charter.lifetime} of active work (see `coco-loops status {charter.loop_id}`).

## E - Escalation

Append-only run-log. Escalate to a human on persistent REJECT, denylist path in
scope, third failure on the same item, or budget at 100%.

---

*Compiled by `coco-loops charter`. Edit this file to refine the contract, or
re-run charter to regenerate it.*
"""


def install(charter: Charter, *, arm: bool = True) -> dict:
    """Validate, write the contract, and (optionally) arm the lifetime cap.

    Uses the configured loops dir and state dir (see coco_loops.paths). Raises
    CharterError if the charter is invalid, so a broken or floor-violating charter
    never reaches disk or arms a loop.
    """
    problems = validate(charter)
    if problems:
        raise CharterError(problems)

    loops_dir = resolve_loops_dir()
    loops_dir.mkdir(parents=True, exist_ok=True)
    contract_path = loops_dir / f"{charter.loop_id}{CONTRACT_SUFFIX}"
    contract_path.write_text(to_contract_markdown(charter), encoding="utf-8")

    armed_cap: int | None = None
    if arm:
        armed_cap = lifetime.parse_duration(charter.lifetime)
        lifetime.arm(charter.loop_id, armed_cap, state_dir=resolve_state_dir())
        kill_switch = kill_switch_path(charter.loop_id)
        if kill_switch.exists():
            kill_switch.unlink()

    return {
        "loop_id": charter.loop_id,
        "contract_path": str(contract_path),
        "armed_cap_seconds": armed_cap,
    }


def load_charter_json(text: str) -> Charter:
    """Parse charter JSON text into a Charter, raising CharterError on bad JSON."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CharterError([f"invalid charter JSON: {exc}"]) from exc
    return Charter.from_dict(data)
