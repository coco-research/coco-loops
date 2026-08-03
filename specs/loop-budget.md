# Loop Budget (Π)

Cost caps, on-exceed behavior, and kill switch for CoCo loops.

**Component:** `Π` (Permissions / budget) in `C = ⟨O, K, I, Y, Φ, A, Π, E⟩`

Read at **start** and **end** of each cycle. Append token estimate to [run-log](./loop-run-log.schema.md) on exit.

---

## Budget table (coding-loop)

Initial caps for Phase P1 L1 (report-only). Adjust only via human edit to this file + run-log note.

| Loop | Max runs/day | Max tokens/day | Max sub-agent spawns/run |
|------|--------------|----------------|--------------------------|
| **coding-loop** | 48 | 500,000 | 4 |

**Sub-agent spawns** count: implementer + verifier + any triage/smoke sub-calls spawned in one cycle. Does not count read-only curl/health probes.

**Token estimate:** CoCo `loop-cost` helper (or `tokens_estimate` in run-log) — sum prompt+completion for the cycle; if unavailable, use conservative upper bound and flag `tokens_estimate_source: heuristic` in run-log.

---

## Measurement window

- **Runs/day:** Rolling 24h count of run-log entries for `loop_id: coding-loop`
- **Tokens/day:** Sum of `tokens_estimate` for same window
- **Spawns/run:** Count at end of single cycle before append

---

## On-exceed behavior

| Threshold | Action |
|-----------|--------|
| **<80%** of any cap | Normal operation (L1: report-only / propose) |
| **≥80%** of daily token cap OR run cap | **Report-only mode** for remainder of window: triage + run-log + M0 update allowed; **no** implementer spawn; **no** fix-proposed that would invoke verifier on large diffs |
| **≥100%** of any cap | **Exit immediately** after appending run-log with `outcome: report-only`, note `budget_exceeded: true` |
| **`loop-pause-all` active** | **Exit immediately** (same as ≥100%) — deliberate human kill |

After ≥100% exit: do not restart until window resets (UTC midnight) or human clears pause flag / raises cap in this file.

---

## Kill switch

| Switch | Location | Effect |
|--------|----------|--------|
| **Per-loop** | `~/.coco/coding-loop.disabled` (touch to pause) | Coding-loop exits at pre-flight (reuse existing daemon pattern) |
| **Global** | `loop-pause-all` flag (env, label, or `~/.coco/loop-pause-all`) | **All** loops exit at budget pre-flight; resume only after human clears flag and documents reason in M0 |

Kill switch is the **deliberate stop** signal (distinct from budget-pause = cost stop, and supervisor revive = liveness — see P1 T9).

---

## L1 cost profile (coding-loop)

| Phase | Typical tokens | Notes |
|-------|----------------|-------|
| No-op (empty watchlist) | <5,000 | Early-exit per [loop-constraints.md](./loop-constraints.md) Rule 4 |
| Report-only / triage | 5,000–30,000 | Findings scan, M0 read/write, run-log |
| Fix-proposed (L1) | 30,000–150,000 | Implementer diff + verifier **dry-run**; no commit |
| Escalated | Variable | Cap still applies; prefer exit after escalate |

---

## Notification policy

Notify human **only on decision**, not every run:

- Budget ≥80% (warning)
- Budget ≥100% or kill switch (pause)
- Escalation (see constraints Rule 3)

Avoid notification fatigue (loop-engineering S1 failure mode).

---

## Promotion gate dependency

Loops **MUST NOT** promote to L3 unattended until this file, [loop-run-log.schema.md](./loop-run-log.schema.md), and a verifier exist and `loop-audit`-style check passes (Phase P1 T8). Mechanical cap: **L < 3** without budget + run-log + verifier.

---

## References

- Constraints (early-exit): [loop-constraints.md](./loop-constraints.md)
- Run-log append: [loop-run-log.schema.md](./loop-run-log.schema.md)
- Constitution I4/I7: [loop-constitution.md](./loop-constitution.md)
