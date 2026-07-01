"""Config-driven path resolution for loop discovery and runtime state."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PathsConfig:
    loops_dir: Path | None = None
    state_dir: Path | None = None


_config = PathsConfig()


def configure(*, loops_dir: Path | str | None = None, state_dir: Path | str | None = None) -> None:
    """Override path resolution (e.g. from CLI flags)."""
    global _config
    if loops_dir is not None:
        _config.loops_dir = Path(loops_dir)
    if state_dir is not None:
        _config.state_dir = Path(state_dir)


def reset_config() -> None:
    global _config
    _config = PathsConfig()


def resolve_loops_dir() -> Path:
    """Loop/contract discovery: CLI > COCO_LOOPS_DIR > ./loops (cwd)."""
    if _config.loops_dir is not None:
        return _config.loops_dir
    env = os.environ.get("COCO_LOOPS_DIR")
    if env:
        return Path(env)
    return Path.cwd() / "loops"


def resolve_state_dir() -> Path:
    """Runtime state: CLI > COCO_LOOPS_HOME > COCO_RUNTIME_DIR (back-compat) > ~/.coco."""
    if _config.state_dir is not None:
        return _config.state_dir
    for key in ("COCO_LOOPS_HOME", "COCO_RUNTIME_DIR"):
        env = os.environ.get(key)
        if env:
            return Path(env)
    return Path(os.environ.get("HOME", Path.home())) / ".coco"


def resolve_run_log_path() -> Path:
    override = os.environ.get("COCO_LOOP_RUN_LOG")
    if override:
        return Path(override)
    return resolve_state_dir() / "loop-run-log.jsonl"


def resolve_constitution_path() -> Path:
    return resolve_loops_dir() / "loop-constitution.md"


def kill_switch_path(loop_id: str) -> Path:
    return resolve_state_dir() / f"{loop_id}.disabled"


def global_pause_path() -> Path:
    return resolve_state_dir() / "loop-pause-all"


def verifier_proven_path() -> Path:
    return resolve_state_dir() / "verifier-proven.json"
