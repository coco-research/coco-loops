"""Tests for L1→L2 promotion gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from coco_loops.promotion_gate import (
    PromotionGateConfig,
    compute_false_positive_rate,
    evaluate_gate,
    main,
)
from coco_loops.run_log import RunLog, RunLogEntry


def _write_artifacts(
    tmp_path: Path,
    *,
    fp_labels: list[str | None],
    verifier_proven: bool = True,
    comprehension: bool = True,
) -> PromotionGateConfig:
    budget = tmp_path / "loop-budget.md"
    budget.write_text("# budget\n", encoding="utf-8")
    run_log_path = tmp_path / "run-log.jsonl"
    log = RunLog(run_log_path)
    for i, verdict in enumerate(fp_labels):
        log.append(
            RunLogEntry(
                run_id=f"run-{i}",
                items_found=1,
                actions_taken=[],
                escalations=[],
                tokens_estimate=100,
                outcome="report-only",
                human_verdict=verdict,
                last_progress="2026-06-30T12:00:00+00:00",
            )
        )
    flag = tmp_path / "verifier-proven.json"
    flag.write_text(
        json.dumps({"verifier_proven": verifier_proven}),
        encoding="utf-8",
    )
    return PromotionGateConfig(
        budget_artifact=budget,
        run_log_path=run_log_path,
        verifier_proven_flag_path=flag,
        comprehension_signed_off=comprehension,
    )


class TestFalsePositiveRate:
    def test_fp_rate_math(self):
        entries = [
            RunLogEntry(
                run_id="1",
                items_found=0,
                human_verdict="correct",
                last_progress="t",
            ),
            RunLogEntry(
                run_id="2",
                items_found=0,
                human_verdict="false_positive",
                last_progress="t",
            ),
            RunLogEntry(
                run_id="3",
                items_found=0,
                human_verdict="noise",
                last_progress="t",
            ),
            RunLogEntry(
                run_id="4",
                items_found=0,
                human_verdict=None,
                last_progress="t",
            ),
        ]
        rate, labeled = compute_false_positive_rate(entries)
        assert labeled == 3
        assert rate == pytest.approx(2 / 3)


class TestPromotionGate:
    def test_pass_when_artifacts_and_low_fp(self, tmp_path: Path):
        config = _write_artifacts(
            tmp_path,
            fp_labels=["correct"] * 9 + ["false_positive"],
        )
        verdict = evaluate_gate(config)
        assert verdict.passed is True
        assert verdict.false_positive_rate == pytest.approx(0.1)
        assert verdict.labeled_entries == 10

    def test_fail_on_high_fp_rate(self, tmp_path: Path):
        config = _write_artifacts(
            tmp_path,
            fp_labels=["false_positive", "false_positive", "correct"],
        )
        verdict = evaluate_gate(config)
        assert verdict.passed is False
        assert any("False-positive rate" in r for r in verdict.reasons)

    def test_main_exits_nonzero_on_fail(self, tmp_path: Path):
        config = _write_artifacts(tmp_path, fp_labels=["false_positive"] * 3)
        rc = main(
            [
                "--budget",
                str(config.budget_artifact),
                "--run-log",
                str(config.run_log_path),
                "--verifier-proven",
                str(config.verifier_proven_flag_path),
            ]
        )
        assert rc == 1

    def test_cli_module_exits_nonzero_on_fail(self, tmp_path: Path):
        config = _write_artifacts(tmp_path, fp_labels=["false_positive"] * 3)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "coco_loops.promotion_gate",
                "--budget",
                str(config.budget_artifact),
                "--run-log",
                str(config.run_log_path),
                "--verifier-proven",
                str(config.verifier_proven_flag_path),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
