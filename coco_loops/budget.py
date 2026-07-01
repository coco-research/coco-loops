"""Loop budget (Π): token/run caps, pause flag, and cost estimation helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from coco_loops.paths import global_pause_path, resolve_run_log_path, resolve_state_dir

BudgetAction = Literal["act", "report_only", "exit"]

# loop-engineering early-exit: empty watchlist should cost <5k tokens.
EMPTY_WATCHLIST_TOKEN_CEILING = 5_000


@dataclass(frozen=True)
class BudgetCaps:
    max_tokens_day: int
    max_runs_day: int = 0
    max_spawns_per_run: int = 0


@dataclass(frozen=True)
class BudgetSpend:
    tokens_day: int = 0
    runs_day: int = 0
    spawns_this_run: int = 0


def _pct(used: int, cap: int) -> float:
    if cap <= 0:
        return 0.0
    return used / cap


def check(
    spend: BudgetSpend,
    caps: BudgetCaps,
    *,
    pause_all: bool = False,
) -> BudgetAction:
    """Return act | report_only | exit based on spend vs caps."""
    if pause_all:
        return "exit"

    token_pct = _pct(spend.tokens_day, caps.max_tokens_day)
    run_pct = _pct(spend.runs_day, caps.max_runs_day) if caps.max_runs_day > 0 else 0.0

    if token_pct >= 1.0 or run_pct >= 1.0:
        return "exit"
    if token_pct >= 0.8 or run_pct >= 0.8:
        return "report_only"
    return "act"


def estimate_cost(
    *,
    tokens: int,
    model_tier: str = "local",
    usd_per_million_tokens: float | None = None,
) -> dict[str, float | int | str]:
    """Lightweight loop-cost estimate (no live pricing API)."""
    tier_rates = {
        "local": 0.0,
        "qb-nano": 0.45,
        "qb-mini": 2.0,
        "qb-sonnet": 15.0,
    }
    rate = usd_per_million_tokens
    if rate is None:
        rate = tier_rates.get(model_tier, 0.0)
    usd = (tokens / 1_000_000) * rate
    return {
        "tokens": tokens,
        "model_tier": model_tier,
        "usd_per_million_tokens": rate,
        "estimated_usd": round(usd, 6),
    }


def should_early_exit_empty_watchlist(
    items_found: int,
    tokens_used: int,
    *,
    ceiling: int = EMPTY_WATCHLIST_TOKEN_CEILING,
) -> bool:
    """True when there is nothing to watch and spend stayed under the cheap ceiling."""
    return items_found == 0 and tokens_used < ceiling


# coding-loop caps per loops/loop-budget.md (P1 L1).
CODING_LOOP_CAPS = BudgetCaps(max_tokens_day=500_000, max_runs_day=48)


@dataclass(frozen=True)
class BudgetStatus:
    """Pre-flight budget snapshot for a loop cycle."""

    ok: bool
    reason: str
    report_only: bool = False


def _parse_run_log_ts(raw: str, *, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def summarize_loop_spend(
    run_log_path: Path,
    loop_id: str,
    *,
    now: datetime | None = None,
    window_hours: int = 24,
) -> BudgetSpend:
    """Sum tokens_estimate and run count for one loop in a rolling window."""
    anchor = now or datetime.now(timezone.utc)
    cutoff = anchor - timedelta(hours=window_hours)
    tokens_day = 0
    runs_day = 0
    if not run_log_path.exists():
        return BudgetSpend()
    for line in run_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry_loop = entry.get("loop_id") or entry.get("loop")
        if entry_loop != loop_id:
            continue
        ts = _parse_run_log_ts(
            entry.get("ts") or entry.get("timestamp") or "",
            fallback=anchor,
        )
        if ts < cutoff:
            continue
        runs_day += 1
        tokens_day += int(entry.get("tokens_estimate") or 0)
    return BudgetSpend(tokens_day=tokens_day, runs_day=runs_day)


def summarize_coding_loop_spend(
    run_log_path: Path,
    *,
    now: datetime | None = None,
    window_hours: int = 24,
) -> BudgetSpend:
    """Sum tokens_estimate and run count for coding-loop entries in a rolling window."""
    return summarize_loop_spend(
        run_log_path, "coding-loop", now=now, window_hours=window_hours
    )


def check_cycle_budget(
    *,
    run_log_path: Path | None = None,
    pause_all_path: Path | None = None,
    caps: BudgetCaps | None = None,
) -> BudgetStatus:
    """Pre-flight budget check for a loop cycle."""
    log_path = run_log_path or resolve_run_log_path()
    pause_path = pause_all_path or global_pause_path()
    caps = caps or CODING_LOOP_CAPS

    if pause_path.exists():
        return BudgetStatus(ok=False, reason="loop-pause-all active", report_only=False)

    spend = summarize_coding_loop_spend(log_path)
    action = check(spend, caps)

    token_cap = caps.max_tokens_day
    run_cap = caps.max_runs_day
    detail = (
        f"tokens={spend.tokens_day}/{token_cap}, runs={spend.runs_day}/{run_cap}"
    )

    if action == "exit":
        return BudgetStatus(
            ok=False,
            reason=f"budget ≥100% ({detail})",
            report_only=False,
        )
    if action == "report_only":
        return BudgetStatus(
            ok=True,
            reason=f"budget ≥80% ({detail})",
            report_only=True,
        )
    return BudgetStatus(ok=True, reason=f"budget ok ({detail})", report_only=False)
