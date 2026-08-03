# Loop Constraints (Φ)

Per-loop constraints read **pre-flight every cycle** (after kill-switch check, before triage or action). Inherits [loop-constitution.md](./loop-constitution.md) (Layer 0).

**Component:** `Φ` in `C = ⟨O, K, I, Y, Φ, A, Π, E⟩`

**Emit on load:** `constraints loaded: N rules active` (where N = count of applicable rules below + denylist entries).

---

## Pre-flight sequence

1. Check kill switch: `~/.coco/coding-loop.disabled` or global `loop-pause-all` → **exit immediately**
2. Load this document + constitution
3. Count active rules; log `constraints loaded: N rules`
4. If watchlist/task queue empty → **early-exit** (target **<5k tokens**; no implementer spawn)
5. Proceed to triage / implement only if constraints pass for the selected item

---

## Rule 1 — Path denylist (never auto-edit)

The following paths and patterns **MUST NOT** be modified by the loop without **human attendance** and explicit `human_verdict` in the run-log. Verifier auto-**REJECT**s diffs touching these (Constitution I2, I5).

| Pattern | Rationale |
|---------|-----------|
| `.env`, `.env.*` | Secrets / config |
| `**/secrets/**` | Secret stores |
| `**/credentials/**` | Credentials |
| `**/*_key*`, `**/*_secret*` | Key material |
| `.terraform/**` | Infra state |
| `k8s/production/**` | Production K8s |
| `**/migrations/**` | Schema migrations (apply = attended) |
| `auth/**` | Authentication |
| `payments/**`, `billing/**` | Financial |

**Gate behavior:** Task or diff targets denylisted path → `outcome: escalated`, `ESCALATE_HUMAN`, no commit.

---

## Rule 2 — No commit without verifier APPROVE

**MUST NOT** run `git commit`, `git add` + commit, or auto-merge unless the verifier sub-agent has emitted `Verdict: APPROVE` for the **exact diff artifact** in the current `run_id` (Constitution I4).

Maker self-check (V5 ACCEPTANCE) is **not** sufficient. L1 cycles log verifier dry-run only; still no commit.

---

## Rule 3 — Escalate after 3 failures → human (not self-improve)

On the **same item** (same task id / finding id):

- **Attempt 1–2:** Retry with simpler scope or alternate approach
- **Attempt 3:** **Stop.** Append run-log with `outcome: escalated`, record `escalations`, update M0 `last_progress`

**Route escalation to:** run-log + M0 checkpoint + ops log `DEFERRED FOR HUMAN` → **human review**.

**MUST NOT** route to `_escalate_to_self_improve` or the self-improve scheduler (that path is **DISABLED** / no-op until that loop earns L3). Constitution I7.

---

## Rule 4 — Empty watchlist early-exit

If **all** of the following are empty or stale-with-no-action:

- High-priority task queue items (pending, not `done`)
- Unactioned warn/critical findings (`actioned != true`)
- M0 open items requiring code fix

Then: log run-log `outcome: no-op`, minimal token spend (target **<5k tokens**), **exit without** spawning implementer or verifier sub-agents.

---

## Rule 5 — Sovereignty (content egress)

**MUST NOT** send sensitive/org content to unsanctioned public-cloud LLM endpoints (Constitution I3). Permitted: local/MLX, a sanctioned enterprise AI gateway, RESUME STATE allowlist.

Verifier checks outbound hosts on any LLM call in the cycle.

---

## Rule 6 — No git push

**MUST NOT** `git push` (from V5 MUST NOT). Local commit only, and only after verifier APPROVE at L2+.

---

## Rule 7 — Destructive ops attended

Live DB writes (spine apply, purge `--apply`, migration apply on production stores), bulk delete, secret rotation → **escalate to human** before execution (Constitution I5). Snapshot first when human approves.

---

## Rule 8 — Constitution and denylist are read-only

This file's **denylist section** and [loop-constitution.md](./loop-constitution.md) may only be changed by **human** edit in `loops/` with ratification recorded. Loop proposals touching these → verifier **REJECT** (Constitution I2, I6).

---

## Rule 9 — Notify on decision only

Do not notify humans every cycle. Notify on: escalation, budget threshold (≥80%), verifier ESCALATE_HUMAN, supervisor dozing/death (Phase P1 T9).

---

## Inheritance

All loops load this file + constitution at cycle start. Loop-specific constraints MAY be appended in the loop contract doc but **MUST NOT** weaken Layer 0 invariants.

**Coding-loop:** See [coding-loop.contract.md](./coding-loop.contract.md) for task queue paths and L1 propose-only behavior.
