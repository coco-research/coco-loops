"""Human-facing CLI for the loop orchestration framework."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from coco_loops.budget import (
    CODING_LOOP_CAPS,
    BudgetCaps,
    BudgetSpend,
    BudgetStatus,
    check,
    check_cycle_budget,
    summarize_coding_loop_spend,
    summarize_loop_spend,
)
from coco_loops import charter as charter_mod
from coco_loops import lifetime
from coco_loops.paths import (
    configure,
    global_pause_path,
    kill_switch_path,
    resolve_loops_dir,
    resolve_run_log_path,
    resolve_state_dir,
    verifier_proven_path,
)
from coco_loops.promotion_gate import PromotionGateConfig, evaluate_gate
from coco_loops.run_log import RunLog, RunLogEntry

RECENT_ENTRY_LIMIT = 10

CONTRACT_SUFFIX = ".contract.md"


def discover_loops() -> list[dict[str, str]]:
    root = resolve_loops_dir()
    if not root.is_dir():
        return []
    loops: list[dict[str, str]] = []
    for path in sorted(root.glob(f"*{CONTRACT_SUFFIX}")):
        loop_id = path.name[: -len(CONTRACT_SUFFIX)]
        text = path.read_text(encoding="utf-8")
        loops.append(
            {
                "loop_id": loop_id,
                "level": infer_level(text),
                "contract_path": str(path),
            }
        )
    return loops


def infer_level(contract_text: str) -> str:
    explicit = re.search(
        r"(?:level|maturity|Phase P1 maturity)[:\s]*\**\s*L([0-3])\b",
        contract_text,
        re.IGNORECASE,
    )
    if explicit:
        return f"L{explicit.group(1)}"
    if re.search(r"\bL1 only\b", contract_text):
        return "L1"
    table = re.search(r"\|\s*\*\*L([0-3])\*\*\s*\|", contract_text)
    if table:
        return f"L{table.group(1)}"
    layer0 = re.search(r"Layer\s*0", contract_text, re.IGNORECASE)
    if layer0:
        return "L0"
    return "L?"


def loop_state(loop_id: str) -> str:
    if kill_switch_path(loop_id).exists():
        return "paused"
    if global_pause_path().exists():
        return "paused (loop-pause-all)"
    return "running"


def _entry_loop_id(data: dict) -> str | None:
    raw = data.get("loop_id") or data.get("loop")
    return str(raw) if raw else None


def read_entries_for_loop(log: RunLog, loop_id: str) -> list[tuple[RunLogEntry, dict]]:
    """Return (entry, raw) pairs for one loop_id within retention window."""
    if not log.path.exists():
        return []
    retained_ids = {e.run_id for e in log.read()}
    pairs: list[tuple[RunLogEntry, dict]] = []
    for line in log.path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _entry_loop_id(data) != loop_id:
            continue
        entry = RunLogEntry.from_dict(data)
        if entry.run_id in retained_ids:
            pairs.append((entry, data))
    return pairs


def loop_caps(loop_id: str) -> BudgetCaps | None:
    if loop_id == "coding-loop":
        return CODING_LOOP_CAPS
    return None


def promotion_artifacts(loop_id: str) -> dict[str, Path]:
    return {
        "budget": resolve_loops_dir() / "loop-budget.md",
        "run_log": resolve_run_log_path(),
        "verifier_proven": verifier_proven_path(),
    }


def cmd_list(_args: argparse.Namespace) -> int:
    loops = discover_loops()
    if not loops:
        print(f"No loop contracts found in {resolve_loops_dir()}")
        return 0
    print(f"{'LOOP':<20} {'LEVEL':<6} {'STATE':<22} CONTRACT")
    for item in loops:
        loop_id = item["loop_id"]
        contract_name = Path(item["contract_path"]).name
        print(
            f"{loop_id:<20} {item['level']:<6} {loop_state(loop_id):<22} {contract_name}"
        )
    return 0


def _print_lifetime_status(loop_id: str) -> None:
    cap = lifetime.cap_seconds(loop_id)
    if cap is None:
        print("Lifetime: unbounded")
        return
    used = lifetime.used_seconds(loop_id)
    left = lifetime.remaining(loop_id)
    assert left is not None
    print(
        f"Lifetime: {lifetime.fmt_duration(used)} of {lifetime.fmt_duration(cap)} used "
        f"({lifetime.fmt_duration(left)} left)"
    )


def cmd_start(args: argparse.Namespace) -> int:
    loop_id = args.loop
    duration_text = args.for_duration or "4h"
    try:
        cap_seconds = lifetime.parse_duration(duration_text)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    lifetime.arm(loop_id, cap_seconds)
    disabled = kill_switch_path(loop_id)
    if disabled.exists():
        disabled.unlink()
    print(f"armed {loop_id} for {duration_text} (kill-switch cleared)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    loop_id = args.loop
    known = {item["loop_id"] for item in discover_loops()}
    if known and loop_id not in known:
        print(f"Unknown loop: {loop_id}", file=sys.stderr)
        print(f"Known loops: {', '.join(sorted(known))}", file=sys.stderr)
        return 1

    log_path = resolve_run_log_path()
    log = RunLog(log_path)
    pairs = read_entries_for_loop(log, loop_id)
    print(f"Loop: {loop_id} ({loop_state(loop_id)})")

    if not pairs:
        print("Run-log: (no entries yet)")
    else:
        recent = pairs[-RECENT_ENTRY_LIMIT:]
        last_entry, last_raw = pairs[-1]
        print(
            f"Run-log: {len(pairs)} entr{'y' if len(pairs) == 1 else 'ies'} "
            f"(showing last {len(recent)})"
        )
        print(f"Last outcome: {last_entry.outcome}")
        print(f"Last progress: {last_entry.last_progress}")
        if last_entry.human_verdict:
            print(f"Last human_verdict: {last_entry.human_verdict}")
        if last_raw.get("verifier_verdict"):
            print(f"Last verifier_verdict: {last_raw['verifier_verdict']}")
        for entry, raw in reversed(recent):
            extras: list[str] = []
            if raw.get("verifier_verdict"):
                extras.append(f"verifier={raw['verifier_verdict']}")
            if entry.escalations:
                extras.append(f"escalations={len(entry.escalations)}")
            if entry.actions_taken:
                extras.append(f"actions={len(entry.actions_taken)}")
            suffix = f" [{', '.join(extras)}]" if extras else ""
            print(
                f"  {entry.timestamp}  {entry.outcome:14}  "
                f"tokens={entry.tokens_estimate}{suffix}"
            )
            for action in entry.actions_taken:
                if "proposal" in action.lower() or "verifier" in action.lower():
                    print(f"    action: {action}")
            for escalation in entry.escalations:
                print(f"    escalation: {escalation}")

    _print_lifetime_status(loop_id)

    caps = loop_caps(loop_id)
    spend = summarize_loop_spend(log_path, loop_id)
    if caps is None:
        print("Budget: caps not configured for this loop")
        return 0

    action = check(spend, caps, pause_all=global_pause_path().exists())
    print(
        f"Budget: tokens={spend.tokens_day}/{caps.max_tokens_day}, "
        f"runs={spend.runs_day}/{caps.max_runs_day} → {action}"
    )
    if loop_id == "coding-loop":
        status: BudgetStatus = check_cycle_budget(
            run_log_path=log_path,
            pause_all_path=global_pause_path(),
            caps=caps,
        )
        print(f"Pre-flight: {status.reason} (report_only={status.report_only})")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    loop_id = args.loop
    known = {item["loop_id"] for item in discover_loops()}
    if known and loop_id not in known:
        print(f"Unknown loop: {loop_id}", file=sys.stderr)
        return 1

    artifacts = promotion_artifacts(loop_id)
    config = PromotionGateConfig(
        budget_artifact=artifacts["budget"],
        run_log_path=artifacts["run_log"],
        verifier_proven_flag_path=artifacts["verifier_proven"],
        comprehension_signed_off=args.comprehension_signed_off,
    )
    verdict = evaluate_gate(config)

    status = "PASS" if verdict.passed else "FAIL"
    print(f"Promotion gate ({loop_id}): {status}")
    for reason in verdict.reasons:
        print(f"  - {reason}")
    if verdict.false_positive_rate is not None:
        print(
            f"  FP-rate: {verdict.false_positive_rate:.2%} "
            f"over {verdict.labeled_entries} labeled entries"
        )
    return 0 if verdict.passed else 1


def cmd_verify(args: argparse.Namespace) -> int:
    package_root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, "-m", "pytest", "tests/", "-q"]
    if args.verbose:
        cmd = [sys.executable, "-m", "pytest", "tests/", "-v"]
    proc = subprocess.run(cmd, cwd=package_root)
    return proc.returncode


def cmd_charter(args: argparse.Namespace) -> int:
    if args.schema:
        print(json.dumps(charter_mod.CHARTER_SCHEMA, indent=2))
        return 0
    if not args.from_json:
        print("charter: provide --from-json <path|-> (or --schema)", file=sys.stderr)
        return 2

    if args.from_json == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.from_json)
        if not path.exists():
            print(f"charter file not found: {path}", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")

    try:
        charter = charter_mod.load_charter_json(text)
        problems = charter_mod.validate(charter)
    except charter_mod.CharterError as exc:
        problems = exc.problems
        charter = None

    if problems:
        print("charter is invalid:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(charter_mod.render(charter))

    if args.install:
        result = charter_mod.install(charter, arm=not args.no_arm)
        print("")
        print(f"installed: {result['contract_path']}")
        if result["armed_cap_seconds"] is not None:
            print(
                f"armed {charter.loop_id} for {charter.lifetime} "
                "(kill-switch cleared) - propose-only"
            )
            print(f"inspect: coco-loops status {charter.loop_id}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from coco_loops.runner import (
        RunnerConfig,
        cursor_agent_maker,
        file_task_source,
        run,
        run_cycle,
    )

    repo = Path(args.repo) if args.repo else Path.cwd()
    tasks = Path(args.tasks) if args.tasks else (resolve_state_dir() / f"{args.loop}-tasks.json")
    cfg = RunnerConfig(
        loop_id=args.loop,
        repo=repo,
        task_source=file_task_source(tasks),
        maker=cursor_agent_maker(args.model),
        poll_interval_seconds=args.interval,
        max_cycles=1 if args.once else args.max_cycles,
    )

    if args.once:
        result = run_cycle(cfg)
        print(f"{args.loop}: {result.outcome} - {result.detail}")
        return 1 if result.outcome == "error" else 0

    results = run(cfg)
    last = results[-1].outcome if results else "none"
    print(f"{args.loop}: {len(results)} cycle(s); last = {last}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coco-loops",
        description="Inspect and operate the loop orchestration framework (local only).",
    )
    parser.add_argument(
        "--loops-dir",
        type=Path,
        default=None,
        help="Directory containing *.contract.md (default: COCO_LOOPS_DIR or ./loops)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Runtime state dir (default: COCO_LOOPS_HOME or ~/.coco)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List loop contracts, levels, and pause state")

    start_p = sub.add_parser("start", help="Arm a loop lifetime cap and clear kill-switch")
    start_p.add_argument("loop", help="Loop id, e.g. coding-loop")
    start_p.add_argument(
        "--for",
        dest="for_duration",
        default=None,
        metavar="DURATION",
        help="Active-work lifetime cap (default: 4h), e.g. 90s, 45m, 6h, 1h30m",
    )

    status_p = sub.add_parser("status", help="Recent run-log + budget for a loop")
    status_p.add_argument("loop", help="Loop id, e.g. coding-loop")

    promote_p = sub.add_parser("promote", help="Run L1→L2 promotion gate for a loop")
    promote_p.add_argument("loop", help="Loop id, e.g. coding-loop")
    promote_p.add_argument(
        "--comprehension-signed-off",
        action="store_true",
        help="Human attests L1 proposals were read",
    )

    verify_p = sub.add_parser("verify", help="Run loop framework pytest suite")
    verify_p.add_argument("-v", "--verbose", action="store_true")

    charter_p = sub.add_parser(
        "charter",
        help="Validate, render, or install a compiled loop charter (goal -> governed loop)",
    )
    charter_p.add_argument(
        "--from-json",
        dest="from_json",
        metavar="PATH",
        help="Charter JSON file, or - to read from stdin",
    )
    charter_p.add_argument(
        "--schema",
        action="store_true",
        help="Print the charter JSON schema a consumer should fill, then exit",
    )
    charter_p.add_argument(
        "--install",
        action="store_true",
        help="Persist the contract and arm the lifetime cap (propose-only)",
    )
    charter_p.add_argument(
        "--no-arm",
        dest="no_arm",
        action="store_true",
        help="With --install, write the contract but do not arm the lifetime yet",
    )

    run_p = sub.add_parser(
        "run",
        help="Run a chartered loop (propose-only) using cursor-agent + a JSON task queue",
    )
    run_p.add_argument("loop", help="Loop id, e.g. keep-build-green")
    run_p.add_argument(
        "--tasks",
        default=None,
        help="JSON task-queue file (default: <state-dir>/<loop>-tasks.json)",
    )
    run_p.add_argument(
        "--repo",
        default=None,
        help="Repo or dedicated worktree to operate in (default: cwd). Must be clean.",
    )
    run_p.add_argument("--model", default="composer-2.5", help="cursor-agent model")
    run_p.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    run_p.add_argument(
        "--max-cycles",
        dest="max_cycles",
        type=int,
        default=None,
        help="Stop after N cycles (default: until paused/expired)",
    )
    run_p.add_argument(
        "--interval", type=float, default=60.0, help="Seconds to sleep between cycles"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure(loops_dir=args.loops_dir, state_dir=args.state_dir)
    handlers = {
        "list": cmd_list,
        "start": cmd_start,
        "status": cmd_status,
        "promote": cmd_promote,
        "verify": cmd_verify,
        "charter": cmd_charter,
        "run": cmd_run,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
