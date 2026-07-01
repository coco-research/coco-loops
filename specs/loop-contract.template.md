# <LOOP_ID> Agentic Contract

**Registry entry for `<LOOP_ID>`** — a consumer loop under the coco-loops orchestration framework.

**Inherits:** [loop-constitution.md](../specs/loop-constitution.md) (Layer 0, immutable)

**Anatomy:** Kalra & Mora, *Agentic Contracts* (2026) — `C = ⟨O, K, I, Y, Φ, A, Π, E⟩`.

---

## Contract mapping `C = ⟨O, K, I, Y, Φ, A, Π, E⟩`

| Symbol | Name | `<LOOP_ID>` artifact | Notes |
|--------|------|----------------------|-------|
| **O** | Objective | See § Objective below | One bounded improvement per cycle; **L1** = report-only |
| **K** | Context | `<CONTEXT_SOURCES>` | SSOT for operational state |
| **I** | Inputs | `<INPUT_QUEUES>` | Queues, pulses, watchlists read each cycle |
| **Y** | Outputs | `<OUTPUT_ARTIFACTS>` | L1: proposals in run-log; **no** auto-commit |
| **Φ** | Constraints | [loop-constraints.md](../specs/loop-constraints.md) | Read pre-flight every cycle |
| **A** | Acceptance | **Verifier** (`coco_loops.verifier`) | Separate adversary; default REJECT |
| **Π** | Permissions / budget | [loop-budget.md](../specs/loop-budget.md) | Caps, ≥80% report-only, kill switch |
| **E** | Escalation | [loop-run-log.schema.md](../specs/loop-run-log.schema.md) | Append-only audit; escalate → human |

---

## O — Objective

`<OBJECTIVE_STATEMENT>`

**Phase P1 maturity: L<MATURITY_LEVEL> <MODE>** — e.g. L1 report-only: propose fixes with logged verifier dry-run; do not auto-commit until L2 gate PASS.

---

## K — Context

Resolve at cycle start from:

1. `<CONTEXT_SOURCE_1>`
2. `<CONTEXT_SOURCE_2>`
3. Loop framework docs — constitution, constraints, budget (`specs/` or consumer `loops/` dir)

---

## I — Inputs

Read every cycle, in order:

1. `<INPUT_1>`
2. `<INPUT_2>`
3. **Pre-flight:** load [loop-constraints.md](../specs/loop-constraints.md); emit `constraints loaded: N rules`

---

## Y — Outputs

| Output | L1 (current) | L2+ (gated) |
|--------|--------------|-------------|
| Primary artifact | **Proposal** in run-log | Applied after verifier APPROVE |
| Verification | Verifier dry-run verdict logged | Verifier APPROVE required |
| Audit | One run-log JSON line | Same |
| State | Checkpoint with `last_progress` | Same |

---

## Φ — Constraints

**Artifact:** [loop-constraints.md](../specs/loop-constraints.md)

Inherits constitution I1–I8. Loaded pre-flight after kill-switch check.

---

## A — Acceptance (verifier — NOT maker)

**Artifact:** `coco_loops.verifier` + consumer-specific test runner

Checklist (all must pass for APPROVE):

- **Scope** — change matches task intent; smallest diff
- **Constitution** — no I1–I8 violation
- **Tests** — verifier **runs** tests itself; does not trust maker claims
- **Sovereignty** — no unsanctioned egress (I3)
- **Risk** — denylist paths → REJECT or ESCALATE_HUMAN

Maker **MUST NOT** assert final ACCEPTANCE as commit authority (Constitution I8).

---

## Π — Permissions and budget

**Artifact:** [loop-budget.md](../specs/loop-budget.md)

Add a row for `<LOOP_ID>` in the budget table. Kill switch: `~/.coco/<LOOP_ID>.disabled` (or `COCO_LOOPS_HOME`).

---

## E — Escalation

**Artifact:** [loop-run-log.schema.md](../specs/loop-run-log.schema.md)

Escalate when verifier REJECT persists, denylisted path in scope, third failure on same item, or budget ≥100%.

---

## Execution (single cycle)

0. **Budget + constraints** — exit if paused or empty watchlist.
1. **Orient** — read inputs and triage.
2. **Implement** — produce **artifact only** (no self-approved commit).
3. **Verify** — invoke verifier (dry-run at L1); log verdict.
4. **Record** — append run-log; update checkpoint with `last_progress`.

One full cycle per invocation, then exit cleanly.

---

## Maturity (this loop)

| Level | Mode | Commit |
|-------|------|--------|
| **L1** | Report-only / propose-only | Verifier dry-run; no commit |
| **L2** | Assisted auto-fix | Verifier APPROVE required; promotion gate must PASS |
| **L3** | Unattended | All artifacts + 2 weeks L1 FP-rate <20% |

---

## Registering this loop

1. Place `<LOOP_ID>.contract.md` in your loops directory (default `./loops` or set `COCO_LOOPS_DIR`).
2. Wire your daemon/script to call `coco_loops.budget.check_cycle_budget`, `coco_loops.verifier.verify`, and append to run-log via `coco_loops.run_log`.
3. Inspect with `coco-loops list` and `coco-loops status <LOOP_ID>`.

See `examples/coding-loop/` for a worked consumer adapter.
