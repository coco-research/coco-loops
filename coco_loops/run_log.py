"""Append-only loop run-log (E + read-back + heartbeat signal)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_RETENTION_DAYS = 30

REQUIRED_FIELDS = frozenset({
    "run_id",
    "items_found",
    "actions_taken",
    "escalations",
    "tokens_estimate",
    "outcome",
    "human_verdict",
    "last_progress",
})


@dataclass
class RunLogEntry:
    run_id: str
    items_found: int
    loop_id: str | None = None
    actions_taken: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    tokens_estimate: int = 0
    outcome: str = "no-op"
    human_verdict: str | None = None
    last_progress: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.last_progress:
            self.last_progress = self.timestamp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunLogEntry:
        return cls(
            run_id=str(data["run_id"]),
            items_found=int(data.get("items_found", 0)),
            loop_id=(str(data["loop_id"]) if data.get("loop_id") else None),
            actions_taken=list(data.get("actions_taken") or []),
            escalations=list(data.get("escalations") or []),
            tokens_estimate=int(data.get("tokens_estimate", 0)),
            outcome=str(data.get("outcome", "no-op")),
            human_verdict=data.get("human_verdict"),
            last_progress=str(data.get("last_progress") or data.get("timestamp") or ""),
            timestamp=str(data.get("timestamp") or ""),
        )


class RunLog:
    """JSON-lines run log with prune-on-read and append helpers."""

    def __init__(self, path: Path, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self.path = Path(path)
        self.retention_days = retention_days

    def _cutoff(self, now: datetime | None = None) -> datetime:
        anchor = now or datetime.now(timezone.utc)
        return anchor - timedelta(days=self.retention_days)

    def _parse_ts(self, raw: str) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def read(self, *, now: datetime | None = None) -> list[RunLogEntry]:
        if not self.path.exists():
            return []
        cutoff = self._cutoff(now)
        entries: list[RunLogEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            entry = RunLogEntry.from_dict(data)
            ts = self._parse_ts(entry.timestamp)
            if ts is not None and ts < cutoff:
                continue
            entries.append(entry)
        return entries

    def append(self, entry: RunLogEntry | dict[str, Any]) -> RunLogEntry:
        if isinstance(entry, dict):
            missing = REQUIRED_FIELDS - set(entry.keys())
            if missing:
                raise ValueError(f"run-log entry missing required fields: {sorted(missing)}")
            record = RunLogEntry.from_dict(entry)
        else:
            record = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        return record

    def prune(self, *, now: datetime | None = None) -> int:
        """Rewrite the log dropping entries older than retention_days. Returns dropped count."""
        if not self.path.exists():
            return 0
        kept = self.read(now=now)
        dropped = 0
        raw_lines = [
            ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        dropped = len(raw_lines) - len(kept)
        if dropped <= 0:
            return 0
        self.path.write_text(
            "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in kept) + "\n",
            encoding="utf-8",
        )
        return dropped

    def last_progress(self) -> str | None:
        entries = self.read()
        if not entries:
            return None
        return entries[-1].last_progress

    def set_verdict(self, run_id: str, verdict: str) -> bool:
        """Record a human_verdict on the entry with run_id, rewriting the log.

        This is the write-path the promotion gate reads from: a human labels a
        proposal (for example ``correct`` or ``false_positive``) so the L1->L2 gate
        can compute a false-positive rate. Returns True if a matching entry was found.
        """
        if not self.path.exists():
            return False
        lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        found = False
        rewritten: list[str] = []
        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                rewritten.append(line)
                continue
            if str(data.get("run_id")) == str(run_id):
                data["human_verdict"] = verdict
                found = True
            rewritten.append(json.dumps(data, ensure_ascii=False))
        if found:
            self.path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        return found
