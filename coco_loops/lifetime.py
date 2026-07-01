"""Bounded loop lifetime: cap on total active-work time per loop."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from coco_loops.paths import resolve_state_dir

_DURATION_PART = re.compile(r"^(\d+)([hms])", re.IGNORECASE)


def _lifetime_path(loop: str, state_dir: Path | None = None) -> Path:
    root = state_dir or resolve_state_dir()
    return root / f"{loop}.lifetime.json"


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_state(loop: str, *, state_dir: Path | None = None) -> dict | None:
    path = _lifetime_path(loop, state_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def parse_duration(s: str) -> int:
    """Parse a duration string like '6h', '45m', '90s', or '1h30m' into seconds."""
    text = s.strip()
    if not text:
        raise ValueError(f"invalid duration: {s!r}")
    total = 0
    rest = text
    while rest:
        match = _DURATION_PART.match(rest)
        if not match:
            raise ValueError(f"invalid duration: {s!r}")
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "h":
            total += amount * 3600
        elif unit == "m":
            total += amount * 60
        else:
            total += amount
        rest = rest[match.end() :]
    if total <= 0:
        raise ValueError(f"invalid duration: {s!r}")
    return total


def fmt_duration(seconds: int) -> str:
    """Format seconds as a compact duration string (e.g. '3h12m', '45m', '90s')."""
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes:
            return f"{hours}h{minutes}m"
        return f"{hours}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def arm(loop: str, cap_seconds: int, *, state_dir: Path | None = None) -> None:
    """Arm a loop with a lifetime cap; resets used time to zero."""
    path = _lifetime_path(loop, state_dir)
    data = {
        "armed_at": datetime.now(timezone.utc).isoformat(),
        "cap_seconds": int(cap_seconds),
        "used_seconds": 0,
    }
    _atomic_write_json(path, data)


def tick(loop: str, elapsed_seconds: int | float, *, state_dir: Path | None = None) -> None:
    """Add active-work seconds to the loop lifetime counter."""
    path = _lifetime_path(loop, state_dir)
    state = _load_state(loop, state_dir=state_dir)
    if state is None:
        return
    state["used_seconds"] = int(state.get("used_seconds", 0)) + max(0, int(elapsed_seconds))
    _atomic_write_json(path, state)


def used_seconds(loop: str, *, state_dir: Path | None = None) -> int:
    """Return used active-work seconds (0 when unbounded)."""
    state = _load_state(loop, state_dir=state_dir)
    if state is None:
        return 0
    return int(state.get("used_seconds", 0))


def cap_seconds(loop: str, *, state_dir: Path | None = None) -> int | None:
    """Return the lifetime cap in seconds, or None when unbounded."""
    state = _load_state(loop, state_dir=state_dir)
    if state is None:
        return None
    return int(state.get("cap_seconds", 0))


def remaining(loop: str, *, state_dir: Path | None = None) -> int | None:
    """Return remaining seconds before expiry, or None when unbounded."""
    cap = cap_seconds(loop, state_dir=state_dir)
    if cap is None:
        return None
    return max(0, cap - used_seconds(loop, state_dir=state_dir))


def is_expired(loop: str, *, state_dir: Path | None = None) -> bool:
    """True when a lifetime file exists and used time has reached the cap."""
    state = _load_state(loop, state_dir=state_dir)
    if state is None:
        return False
    cap = int(state.get("cap_seconds", 0))
    used = int(state.get("used_seconds", 0))
    return used >= cap


def disarm(loop: str, *, state_dir: Path | None = None) -> None:
    """Remove the lifetime file, returning the loop to unbounded mode."""
    path = _lifetime_path(loop, state_dir)
    if path.exists():
        path.unlink()
