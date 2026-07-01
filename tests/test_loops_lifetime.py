"""Tests for bounded loop lifetime (active-work time cap)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from coco_loops import lifetime


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    root = tmp_path / "coco"
    root.mkdir()
    return root


class TestLifetimeState:
    def test_arm_sets_cap_and_zero_used(self, state_dir: Path):
        lifetime.arm("demo-loop", 3600, state_dir=state_dir)

        assert lifetime.used_seconds("demo-loop", state_dir=state_dir) == 0
        assert lifetime.cap_seconds("demo-loop", state_dir=state_dir) == 3600

    def test_tick_accumulates_used_seconds(self, state_dir: Path):
        lifetime.arm("demo-loop", 3600, state_dir=state_dir)

        lifetime.tick("demo-loop", 60, state_dir=state_dir)
        lifetime.tick("demo-loop", 60, state_dir=state_dir)

        assert lifetime.used_seconds("demo-loop", state_dir=state_dir) == 120

    def test_is_expired_when_used_reaches_cap(self, state_dir: Path):
        lifetime.arm("demo-loop", 120, state_dir=state_dir)

        lifetime.tick("demo-loop", 60, state_dir=state_dir)
        assert lifetime.is_expired("demo-loop", state_dir=state_dir) is False

        lifetime.tick("demo-loop", 60, state_dir=state_dir)
        assert lifetime.is_expired("demo-loop", state_dir=state_dir) is True

    def test_unbounded_when_no_lifetime_file(self, state_dir: Path):
        assert lifetime.is_expired("demo-loop", state_dir=state_dir) is False
        assert lifetime.cap_seconds("demo-loop", state_dir=state_dir) is None
        assert lifetime.remaining("demo-loop", state_dir=state_dir) is None

        lifetime.tick("demo-loop", 999, state_dir=state_dir)

        assert not (state_dir / "demo-loop.lifetime.json").exists()
        assert lifetime.is_expired("demo-loop", state_dir=state_dir) is False

    def test_gap_with_no_ticks_does_not_advance_used(self, state_dir: Path):
        lifetime.arm("demo-loop", 3600, state_dir=state_dir)
        lifetime.tick("demo-loop", 30, state_dir=state_dir)

        used_before = lifetime.used_seconds("demo-loop", state_dir=state_dir)
        time.sleep(0.01)
        assert lifetime.used_seconds("demo-loop", state_dir=state_dir) == used_before
        assert lifetime.is_expired("demo-loop", state_dir=state_dir) is False


class TestDurationParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("6h", 21600),
            ("1h30m", 5400),
            ("45m", 2700),
            ("90s", 90),
        ],
    )
    def test_parse_duration_valid(self, text: str, expected: int):
        assert lifetime.parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["", "garbage", "6x", "1h30"])
    def test_parse_duration_garbage_raises(self, text: str):
        with pytest.raises(ValueError):
            lifetime.parse_duration(text)
