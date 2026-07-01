"""Executable L1→L2 promotion gate — hard PASS/FAIL with non-zero exit on FAIL."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from coco_loops.run_log import RunLog

DEFAULT_FP_RATE_CEILING = 0.20


@dataclass
class PromotionGateConfig:
    budget_artifact: Path
    run_log_path: Path
    verifier_proven_flag_path: Path
    fp_rate_ceiling: float = DEFAULT_FP_RATE_CEILING
    window_days: int = 14
    comprehension_signed_off: bool = False


@dataclass
class PromotionGateVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    false_positive_rate: float | None = None
    labeled_entries: int = 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "false_positive_rate": self.false_positive_rate,
            "labeled_entries": self.labeled_entries,
        }


def compute_false_positive_rate(entries: list) -> tuple[float | None, int]:
    """FP-rate = false_positive labels / all entries with a human_verdict."""
    labeled = [e for e in entries if getattr(e, "human_verdict", None)]
    if not labeled:
        return None, 0
    false_positives = sum(
        1 for e in labeled if str(e.human_verdict).lower() in {"false_positive", "fp", "noise"}
    )
    return false_positives / len(labeled), len(labeled)


def evaluate_gate(config: PromotionGateConfig) -> PromotionGateVerdict:
    reasons: list[str] = []

    if not config.budget_artifact.exists():
        reasons.append(f"Missing budget artifact: {config.budget_artifact}")
    if not config.run_log_path.exists():
        reasons.append(f"Missing run-log artifact: {config.run_log_path}")
    if not config.verifier_proven_flag_path.exists():
        reasons.append(f"Missing verifier-proven flag: {config.verifier_proven_flag_path}")

    run_log = RunLog(config.run_log_path, retention_days=config.window_days)
    entries = run_log.read()
    fp_rate, labeled = compute_false_positive_rate(entries)

    if labeled == 0:
        reasons.append("No human_verdict labels in run-log window — cannot compute FP-rate.")
    elif fp_rate is not None and fp_rate >= config.fp_rate_ceiling:
        reasons.append(
            f"False-positive rate {fp_rate:.2%} >= ceiling {config.fp_rate_ceiling:.0%}."
        )

    flag_data: dict = {}
    if config.verifier_proven_flag_path.exists():
        try:
            flag_data = json.loads(config.verifier_proven_flag_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reasons.append("Verifier-proven flag is not valid JSON.")
    if not flag_data.get("verifier_proven"):
        reasons.append("verifier_proven flag is not set true.")

    if not config.comprehension_signed_off:
        reasons.append("Human comprehension sign-off missing for L1 proposals.")

    passed = len(reasons) == 0
    return PromotionGateVerdict(
        passed=passed,
        reasons=reasons,
        false_positive_rate=fp_rate,
        labeled_entries=labeled,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L1→L2 promotion gate")
    parser.add_argument("--budget", type=Path, required=True)
    parser.add_argument("--run-log", type=Path, required=True)
    parser.add_argument("--verifier-proven", type=Path, required=True)
    parser.add_argument("--fp-ceiling", type=float, default=DEFAULT_FP_RATE_CEILING)
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument(
        "--comprehension-signed-off",
        action="store_true",
        help="Human attests L1 proposals were read",
    )
    parser.add_argument("--json", action="store_true", help="Emit verdict JSON to stdout")
    args = parser.parse_args(argv)

    config = PromotionGateConfig(
        budget_artifact=args.budget,
        run_log_path=args.run_log,
        verifier_proven_flag_path=args.verifier_proven,
        fp_rate_ceiling=args.fp_ceiling,
        window_days=args.window_days,
        comprehension_signed_off=args.comprehension_signed_off,
    )
    verdict = evaluate_gate(config)

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2))
    else:
        status = "PASS" if verdict.passed else "FAIL"
        print(f"Promotion gate: {status}")
        for reason in verdict.reasons:
            print(f"  - {reason}")
        if verdict.false_positive_rate is not None:
            print(
                f"  FP-rate: {verdict.false_positive_rate:.2%} "
                f"over {verdict.labeled_entries} labeled entries"
            )

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
