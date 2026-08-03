# Loop Constitution (Layer 0 — Immutable)

This document defines invariants that **every** CoCo loop inherits. No loop, skill, daemon, or self-evolution cycle may edit, disable, or bypass these rules. Self-evolution may **propose** a change; only a **human** may ratify an amendment to this file.

**Status:** Immutable floor. Promoted from advisory `brain_principles` to **enforced** governance (Phase P1, T0b).

---

## Machine-checkable invariants

Each invariant is phrased so an automated gate (verifier, CI, or pre-commit hook) can evaluate a proposal or diff and return PASS / REJECT without human judgment.

### I1 — Verifier must remain enabled

**Predicate:** No change SHALL disable, bypass, stub-out, or no-op the loop's verifier sub-agent or its invocation on any code path that leads to `git commit`, `git add` of tracked source, or auto-merge.

**Gate check:** Diff touches `verifier`, sets `VERIFIER_ENABLED=false`, removes verifier hook before commit, or replaces verifier with maker self-approval → **REJECT**.

### I2 — Denylist and constitution are read-only to loops

**Predicate:** No loop process SHALL write, delete, or amend `loops/loop-constitution.md`, `loops/loop-constraints.md` (denylist section), or any path listed in the denylist, except via a human-attended change with explicit ratification recorded in the run-log (`human_verdict`).

**Gate check:** Diff modifies `loops/loop-constitution.md`, `loops/loop-constraints.md` denylist paths, or a denylisted path without `human_verdict: ratified` in the same run → **REJECT**.

### I3 — Sovereignty: no raw public cloud for content

**Predicate:** No loop SHALL send organizational, client, or otherwise sensitive **content** to unsanctioned public-cloud LLM endpoints (`api.openai.com`, `api.anthropic.com`, or equivalent direct egress). Permitted paths: **local/MLX**, **a sanctioned enterprise AI gateway**, and other explicitly allowlisted sanctioned backends per project RESUME STATE.

**Gate check:** Code or config introduces a call to a non-allowlisted host for content generation/embeddings on sensitive payloads → **REJECT**. (Host allowlist enforced by sovereignty guard + verifier review.)

### I4 — No commit without verifier APPROVE

**Predicate:** No loop SHALL execute `git commit`, auto-merge, or equivalent durable VCS write unless the verifier sub-agent has emitted `Verdict: APPROVE` for that exact diff artifact in the current run.

**Gate check:** Commit attempted without a logged verifier APPROVE for matching `run_id` / diff hash → **REJECT** and block commit hook.

### I5 — Destructive and spine operations are attended

**Predicate:** No loop SHALL perform irreversible or high-blast-radius operations without human attendance: live `platform.db` / `knowledge.db` / `hub.db` writes (except idempotent ingest under flock), migration **apply** on live stores, bulk purge/delete, secret rotation, `git push`, production infra changes, or spine entity merge/quarantine **apply**.

**Gate check:** Diff or runtime action matches destructive/spine pattern (migration apply, purge script `--apply` on live path, `git push`, truncate) without `outcome: escalated` + `human_verdict` → **REJECT** or force escalate before execution.

### I6 — Constitution amendments are human-ratified only

**Predicate:** Self-evolution, skill promotion, or any autonomous agent MAY **propose** an amendment to this constitution or the denylist. Such a proposal SHALL NOT take effect until a human edits the canonical file in `loops/` and records ratification (`human_verdict`, dated ops-log entry, or signed PR merge by owner).

**Gate check:** Autonomous write to `loop-constitution.md` or denylist without human merge → **REJECT**. Proposal-only artifacts (run-log `fix-proposed`, M0 checkpoint) are allowed.

### I7 — Self-improve does not override constitution

**Predicate:** The self-improve scheduler and its merge gate SHALL remain **DISABLED** or **proposal-only** until that loop earns L3 under the promotion gate. No self-improve cycle SHALL auto-merge code that violates I1–I6.

**Gate check:** Self-improve auto-merge enabled while coding-loop is below L2 gate PASS → **REJECT** (config / feature flag).

### I8 — Loop must not be its own final authority

**Predicate:** The implementer/maker sub-agent SHALL NOT emit the sole commit decision, final ACCEPTANCE PASS, or `done.add` for production-impacting work. Final authority for commit = verifier (I4); final authority for constitution = human (I6).

**Gate check:** Code path where maker sets `ACCEPTANCE: PASS` and commits without verifier → **REJECT** (closes V5 self-check anti-pattern).

---

## Enforcement

| Mechanism | Behavior |
|-----------|----------|
| **`backend/app/loops/verifier.py`** | Separate adversary sub-agent; default **REJECT**. Runs tests in isolated worktree. Auto-**REJECT**s any proposal whose diff, config, or stated intent touches I1–I8 (constitution, denylist, verifier disable, unsanctioned egress, commit-without-approve, unattended destructive ops). |
| **Pre-commit hook (planned)** | Refuse commit if verifier verdict ≠ APPROVE for staged diff. |
| **L1 dry-run** | Every L1 proposal receives logged APPROVE/REJECT; no commit regardless (report-only). |
| **Human ratification** | Only path to amend this file: human edit + review; recorded in run-log and git history. |

---

## Inheritance

Every loop contract (e.g. [coding-loop.contract.md](./coding-loop.contract.md)) **MUST** declare:

```text
Inherits: loops/loop-constitution.md (Layer 0, immutable)
```

Pre-flight (Φ) and verifier (A) **MUST** load and enforce this document each cycle before triage or action.
