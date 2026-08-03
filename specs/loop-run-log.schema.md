# Loop Run-Log Schema (E — audit + heartbeat)

Append-only audit trail: one JSON object per cycle per loop. Feeds escalation (**E**), promotion gate FP-rate (T8), and supervisor liveness (T9 `last_progress`).

**Storage (runtime):** `~/.coco/loop-run-log.jsonl` (append-only JSONL) or equivalent; canonical schema defined here in `loops/`.

**Retention:** Prune entries **older than 30 days** on append (same policy as loop-engineering `append-run-log.mjs`).

---

## Record shape

Each line is one JSON object:

```json
{
  "run_id": "string (required)",
  "loop_id": "string (required, e.g. coding-loop)",
  "timestamp": "ISO-8601 UTC (required)",
  "items_found": "integer (required, >= 0)",
  "actions_taken": ["string"],
  "escalations": ["string"],
  "tokens_estimate": "integer (required, >= 0)",
  "outcome": "no-op | report-only | fix-proposed | escalated | paused | expired | error | maker-error | blocked (required)",
  "human_verdict": "string | null",
  "last_progress": "string (required)"
}
```

---

## Field definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `run_id` | string | yes | Unique id for this cycle (UUID or `{loop_id}-{timestamp}`) |
| `loop_id` | string | yes | Registry id, e.g. `coding-loop` |
| `timestamp` | string | yes | Cycle end time, ISO-8601 UTC |
| `items_found` | integer | yes | Count of tasks/findings/M0 items triaged this cycle |
| `actions_taken` | array of string | yes (may be empty) | Short action lines, e.g. `"verifier:dry-run REJECT"`, `"M0:checkpoint"`, `"TaskCreate: finding-42"` |
| `escalations` | array of string | yes (may be empty) | Reasons/items escalated to human; empty if none |
| `tokens_estimate` | integer | yes | Total tokens consumed this cycle (see [loop-budget.md](./loop-budget.md)) |
| `outcome` | enum | yes | Terminal classification (see below) |
| `human_verdict` | string \| null | no | Human accuracy label for promotion gate: e.g. `correct`, `false-positive`, `missed`, `ratified`, `deferred`. Required when human reviewed an escalation or L1 proposal |
| `last_progress` | string | yes | **Heartbeat signal** for supervisor (T9): one line describing last meaningful progress, e.g. task id advanced, proposal written, or `no-op-empty-watchlist` |

### Optional extensions (non-breaking)

| Field | Type | Description |
|-------|------|-------------|
| `verifier_verdict` | string | `APPROVE` \| `REJECT` \| `ESCALATE_HUMAN` |
| `budget_exceeded` | boolean | true if exit due to ≥100% cap or kill switch |
| `tokens_estimate_source` | string | `metered` \| `heuristic` |
| `duration_s` | number | Wall-clock cycle duration |
| `pattern` | string | Legacy loop-engineering pattern id if applicable |

---

## `outcome` enum

| Value | Meaning |
|-------|---------|
| `no-op` | Empty watchlist; early-exit (<5k tokens); nothing to do. Also: the maker ran and legitimately produced no diff (`files_changed` stayed empty) — **never** used when the maker failed to run at all; see `maker-error` / `blocked` below |
| `report-only` | Triage/state updated; budget ≥80% forced report-only; or L1 observation only |
| `fix-proposed` | Diff artifact + verifier dry-run logged; **no commit** (L1) |
| `escalated` | Stopped; human handoff required (3rd failure, denylist, destructive, verifier ESCALATE_HUMAN) |
| `paused` | Governance gate stopped the cycle before the maker ran: kill-switch, global pause, dirty tree, or the maker-error circuit breaker tripped (`coco_loops/runner.py` `run()`) |
| `expired` | Lifetime budget exhausted; loop self-stopped (kill-switch touched) |
| `error` | Runner-level exception (bug, IO failure, verifier crash unrelated to the maker itself) — never a silent success |
| `maker-error` | The maker CLI ran but failed: non-zero exit, crash, or missing binary. Distinct from `no-op` on purpose — the maker never got to decide anything. Real stderr is carried into `actions_taken` / `escalations` |
| `blocked` | The maker could not authenticate (recognisable auth error in its output), or the pre-flight probe run before the first cycle failed. Operator action required (e.g. `cursor-agent login`); when caught at pre-flight, **no lifetime budget or task-queue entry is spent** |

At L2+, a committed fix may still log `fix-proposed` with `verifier_verdict: APPROVE` and `actions_taken` including `git:commit <hash>` — or add future value `fix-applied` when promotion gate extends schema.

---

## `human_verdict` (promotion gate / T8)

Used to compute false-positive rate over 2 weeks L1:

- **`correct`** — Proposal/escalation was accurate; human agrees
- **`false-positive`** — Loop flagged or proposed unnecessary work
- **`missed`** — Human found issue loop should have caught
- **`ratified`** — Human approved constitution/denylist/amendment or attended destructive op
- **`deferred`** — Human acknowledged; no action now
- **`null`** — Not yet reviewed (L1 proposals recur until reviewed)

Promotion L1→L2 requires **<20% false-positive** rate over 2 weeks + comprehension check (human confirms proposals were read).

---

## `last_progress` (supervisor / T9)

Supervisor reads the latest entry per `loop_id` and compares `last_progress` + `timestamp` to loop cadence:

- **Death:** process gone → revive (up to K times) → escalate
- **Dozing:** process alive but `last_progress` unchanged across N intervals → revive or escalate

**MUST** update every cycle, including `no-op` (e.g. `last_progress: "no-op-empty-watchlist"`).

---

## Append rules

1. **Append only** — never rewrite or delete in place (except prune job)
2. **One record per cycle** — written at cycle end (budget step)
3. **Valid JSON** — one object per line (JSONL)
4. **Prune on append** — drop records with `timestamp` older than **30 days**

---

## Example entries

**No-op (empty watchlist):**

```json
{
  "run_id": "coding-loop-20260630T120000Z",
  "loop_id": "coding-loop",
  "timestamp": "2026-06-30T12:00:05Z",
  "items_found": 0,
  "actions_taken": [],
  "escalations": [],
  "tokens_estimate": 3200,
  "outcome": "no-op",
  "human_verdict": null,
  "last_progress": "no-op-empty-watchlist"
}
```

**L1 fix-proposed:**

```json
{
  "run_id": "coding-loop-20260630T140000Z",
  "loop_id": "coding-loop",
  "timestamp": "2026-06-30T14:22:11Z",
  "items_found": 3,
  "actions_taken": [
    "triage: finding-17 high",
    "implementer: diff artifact tasks-router.py",
    "verifier:dry-run REJECT scope-mismatch"
  ],
  "escalations": [],
  "tokens_estimate": 87400,
  "outcome": "fix-proposed",
  "human_verdict": null,
  "last_progress": "finding-17 proposal v2 pending human review",
  "verifier_verdict": "REJECT"
}
```

**Escalated (3rd failure):**

```json
{
  "run_id": "coding-loop-20260630T160000Z",
  "loop_id": "coding-loop",
  "timestamp": "2026-06-30T16:05:00Z",
  "items_found": 1,
  "actions_taken": ["implementer: attempt 3 failed tsc"],
  "escalations": ["task-88: tsc fail 3x on ChatPage.tsx"],
  "tokens_estimate": 62000,
  "outcome": "escalated",
  "human_verdict": null,
  "last_progress": "task-88 escalated after 3 failures"
}
```

---

## References

- Escalation routing: [loop-constraints.md](./loop-constraints.md) Rule 3
- Budget token reporting: [loop-budget.md](./loop-budget.md)
- Contract: [coding-loop.contract.md](./coding-loop.contract.md)
