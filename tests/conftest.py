"""Shared pytest configuration for coco-loops."""

from __future__ import annotations

from pathlib import Path

import pytest

from coco_loops.paths import reset_config

_SPECS_DIR = Path(__file__).resolve().parents[1] / "specs"


@pytest.fixture(autouse=True)
def _default_loops_dir(monkeypatch: pytest.MonkeyPatch):
    """Point default loop discovery at packaged specs (constitution for verifier)."""
    monkeypatch.setenv("COCO_LOOPS_DIR", str(_SPECS_DIR))
    reset_config()
    yield
    reset_config()
