# Coding-Loop Agentic Contract

**McKinsey-internal.** Registry entry for the CoCo **coding-loop** — the first loop closed end-to-end under the loop framework (Phase P1).

**Inherits:** [loop-constitution.md](./loop-constitution.md) (Layer 0, immutable)

**V5 source:** `~/.coco/claude-loop-prompt.txt` (Autonomous Loop — Agentic Contract v5.0). Anatomy: Kalra & Mora, *Agentic Contracts* (2026) — `C = ⟨O, K, I, Y, Φ, A, Π, E⟩`.

---

## Critical deviation from V5

**`A` is the ONE component where we deviate from V5.**

In V5, **ACCEPTANCE** (Section ACCEPTANCE in `claude-loop-prompt.txt`) is a **maker self-check**: the implementer evaluates predicates ("`git log -1` shows a new commit", "screenshots taken", "ACCEPTANCE: PASS") and records PASS/FAIL in the ops log **before claiming cycle complete**.

In the coding-loop contract, the **postcondition moves OUT of the maker's self-check INTO a separate verifier sub-agent** (loop-engineering's correction to *maker grades itself*). The maker produces a **diff artifact only**; it does **not** hold commit authority. The verifier runs acceptance checks **independently** (tests, scope, constitution, sovereignty) and emits `Verdict: APPROVE | REJECT | ESCALATE_HUMAN`. **No commit without verifier APPROVE** (Constitution I4).

---

## Contract mapping `C = ⟨O, K, I, Y, Φ, A, Π, E⟩`

| Symbol | Name | Coding-loop artifact | Notes |
|--------|------|----------------------|-------|
| **O** | Objective | See § Objective below | One bounded improvement per cycle; L1 = report-only (propose, no commit) |
| **K** | Context | M0 `operational_thread` + ops log RESUME STATE | SSOT via `POST/GET /api/brain/checkpoint`; retire duplicate STATE files |
| **I** | Inputs | Findings queue, task queue, tick pulse | `coding-loop-tasks-dynamic.json`, findings-bridge output, `tick.py --brief` |
| **Y** | Outputs | Diff artifact, run-log entry, M0 checkpoint, TaskCreate | L1: proposed diff + reasoning in run-log/M0; **no** auto-commit |
| **Φ** | Constraints | [loop-constraints.md](./loop-constraints.md) | Read pre-flight every cycle; denylist + escalation caps |
| **A** | Acceptance | **Verifier sub-agent** (`backend/app/loops/verifier.py` + verifier skill) | **Separate adversary**; default REJECT; NOT maker self-check |
| **Π** | Permissions / budget | [loop-budget.md](./loop-budget.md) | Caps, ≥80% report-only, kill switch |
| **E** | Escalation | [loop-run-log.schema.md](./loop-run-log.schema.md) + human handoff | Append-only audit; escalate → human, NOT disabled self-improve path |

---

## O — Objective

Ship one real, verifiable improvement per cycle: fix a broken thing, close a queued item, or escalate what cannot be safely completed. **Phase P1 maturity: L1 report-only** — propose fixes with logged verifier dry-run; do not auto-commit until L2 gate PASS.

Aligned with V5 OBJECTIVE (`claude-loop-prompt.txt` lines 12–13), minus unverified auto-commit.

---

## K — Context

Resolve at cycle start from:

1. **M0 operational thread** — `/api/brain/checkpoint` (project slug from RESUME STATE)
2. **Ops log RESUME STATE** — canonical repo root, ports, data-store paths, sovereignty policy
3. **Loop framework docs** — constitution, constraints, budget (this directory)

Sovereignty policy (from V5 CONTEXT): local-first LLM routing; no raw public-cloud APIs for sensitive/org content unless explicitly allowlisted (Constitution I3).

---

## I — Inputs

Read every cycle, in order (from V5 INPUTS, adapted):

1. M0 thread + ops log RESUME STATE + last run-log entry
2. Pending task queue (`coding-loop-tasks-dynamic.json` / TaskList)
3. Findings queue — last 20 unactioned items (`severity`, `auto_task`, `actioned`)
4. Optional pulse: `python3 ~/.coco/tick.py --brief` → `~/.coco/last-tick.json`
5. **Pre-flight:** load [loop-constraints.md](./loop-constraints.md); emit `constraints loaded: N rules`

---

## Y — Outputs

Produce before exit:

| Output | L1 (current) | L2+ (gated) |
|--------|--------------|-------------|
| Code change | **Diff artifact + proposal** in run-log/M0 | Commit after verifier APPROVE |
| Findings | TaskCreate warn/critical; mark `actioned` | Same |
| Verification | Verifier dry-run verdict logged | Verifier APPROVE required for commit |
| Audit | One [run-log](./loop-run-log.schema.md) JSON line | Same |
| State | M0 checkpoint (`last_progress`, open items) | Same |

V5 outputs (commit, screenshots, ops log) remain required for L2+; L1 substitutes **fix-proposed** outcome for commit.

---

## Φ — Constraints

**Artifact:** [loop-constraints.md](./loop-constraints.md)

Inherits constitution I1–I8. Loaded pre-flight after kill-switch check. Includes path denylist, no commit without verifier APPROVE, escalate-after-3 → human, empty watchlist early-exit.

---

## A — Acceptance (verifier — NOT maker)

**Artifact:** `backend/app/loops/verifier.py` + verifier skill (separate sub-agent)

Checklist (all must pass for APPROVE):

- **Scope** — change matches task/findings intent; smallest diff
- **Constitution** — no I1–I8 violation
- **Tests** — verifier **runs** pytest/tsc/build itself in isolated worktree; does not trust maker claims
- **Sovereignty** — no unsanctioned egress (I3)
- **Risk** — denylist paths → REJECT or ESCALATE_HUMAN

Output format:

```text
## Verdict: APPROVE | REJECT | ESCALATE_HUMAN
Evidence: ...
```

Maker **MUST NOT** assert `ACCEPTANCE: PASS` as commit authority (Constitution I8).

---

## Π — Permissions and budget

**Artifact:** [loop-budget.md](./loop-budget.md)

Allowed (from V5 PERMISSIONS, subject to budget): read/write canonical repo, `~/.coco/*` runtime, local git commit **only after verifier APPROVE**, health probes, pytest/tsc, sqlite read (write with snapshot + attended if spine).

Denied: `git push`, denylisted paths, unattended destructive ops, unsanctioned cloud LLM on sensitive content.

Budget enforced at cycle start and end; see budget doc for caps and kill switch.

---

## E — Escalation

**Artifact:** [loop-run-log.schema.md](./loop-run-log.schema.md)

Escalate (do not force completion) when:

- Verifier REJECT persists after retry
- Acceptance cannot be verified
- Destructive/spine action needed (Constitution I5)
- Denylisted path in task scope
- Third failure on same item (Φ)
- Budget ≥100% or `loop-pause-all`

On escalate: append run-log with `outcome: escalated`, M0 checkpoint, ops log `DEFERRED FOR HUMAN: …`. Route to **human** — **NOT** `_escalate_to_self_improve` (disabled/no-op in current runtime).

---

## Execution (single cycle)

Adapted from V5 EXECUTION (`claude-loop-prompt.txt` lines 95–113):

0. **Budget + constraints** — read [loop-budget.md](./loop-budget.md), [loop-constraints.md](./loop-constraints.md); exit if paused or empty watchlist (<5k tokens).
1. **Situation** — `tick.py --brief`; critical health → primary_action first.
2. **Orient** — M0, task queue, findings; TaskCreate warn/critical.
3. **Implement** — pick highest priority item; produce **diff artifact only** (no self-approved commit).
4. **Verify** — invoke **verifier sub-agent** (dry-run at L1); log verdict.
5. **Record** — append run-log; M0 checkpoint with `last_progress`; L1: `outcome: fix-proposed` or `report-only`.

One full cycle per invocation, then exit cleanly.

---

## Maturity (this loop)

| Level | Mode | Commit |
|-------|------|--------|
| **L1** | Report-only / propose-only | Verifier dry-run; no commit |
| **L2** | Assisted auto-fix | Verifier APPROVE required; promotion gate must PASS |
| **L3** | Unattended | All artifacts + 2 weeks L1 FP-rate <20%; not enabled in P1 |

Week-one rule: **L1 only** until promotion gate (P1 T8) passes.

---

## References

- V5 contract: `~/.coco/claude-loop-prompt.txt`
- Constitution: [loop-constitution.md](./loop-constitution.md)
- Phase plan: `.planning/phases/P1-coding-loop-close/PLAN.md`
- Teardown standard: `.planning/LOOP_ENGINEERING_TEARDOWN.md`
