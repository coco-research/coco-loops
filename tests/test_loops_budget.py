"""Tests for loop budget helpers."""

from __future__ import annotations

from coco_loops.budget import (
    BudgetCaps,
    BudgetSpend,
    check,
    estimate_cost,
    should_early_exit_empty_watchlist,
)


class TestBudgetThresholds:
    def test_act_under_80_percent(self):
        caps = BudgetCaps(max_tokens_day=1000, max_runs_day=10)
        spend = BudgetSpend(tokens_day=500, runs_day=2)
        assert check(spend, caps) == "act"

    def test_report_only_at_80_percent_tokens(self):
        caps = BudgetCaps(max_tokens_day=1000)
        spend = BudgetSpend(tokens_day=800)
        assert check(spend, caps) == "report_only"

    def test_report_only_at_80_percent_runs(self):
        caps = BudgetCaps(max_tokens_day=10_000, max_runs_day=10)
        spend = BudgetSpend(tokens_day=100, runs_day=8)
        assert check(spend, caps) == "report_only"

    def test_exit_at_100_percent_tokens(self):
        caps = BudgetCaps(max_tokens_day=1000)
        spend = BudgetSpend(tokens_day=1000)
        assert check(spend, caps) == "exit"

    def test_exit_on_pause_flag(self):
        caps = BudgetCaps(max_tokens_day=1000)
        spend = BudgetSpend(tokens_day=10)
        assert check(spend, caps, pause_all=True) == "exit"


class TestBudgetHelpers:
    def test_estimate_cost_local_is_free(self):
        est = estimate_cost(tokens=50_000, model_tier="local")
        assert est["estimated_usd"] == 0.0
        assert est["tokens"] == 50_000

    def test_empty_watchlist_early_exit(self):
        assert should_early_exit_empty_watchlist(items_found=0, tokens_used=4999) is True
        assert should_early_exit_empty_watchlist(items_found=0, tokens_used=5000) is False
        assert should_early_exit_empty_watchlist(items_found=1, tokens_used=100) is False
