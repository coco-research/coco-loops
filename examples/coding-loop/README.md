# Coding-Loop Example (CoCo — customer #1)

This directory shows how **CoCo** wired its `coding-loop` daemon to the generic `coco-loops` framework.

## Files

| File | Purpose |
|------|---------|
| [coding-loop.contract.md](./coding-loop.contract.md) | Concrete agentic contract `C = ⟨O, K, I, Y, Φ, A, Π, E⟩` for the coding-loop |
| [coding-loop.phase1.patch](./coding-loop.phase1.patch) | Phase-1 wiring patch: PROPOSE_ONLY mode, verifier hook, budget pre-flight, run-log append |

## How CoCo consumes coco-loops

1. **Contract discovery** — copy or symlink `coding-loop.contract.md` into the repo's `loops/` directory (or set `COCO_LOOPS_DIR`).
2. **Runtime state** — kill-switch at `~/.coco/coding-loop.disabled`, run-log at `~/.coco/loop-run-log.jsonl` (override with `COCO_LOOPS_HOME`).
3. **Framework calls** — the patched daemon imports:
   - `coco_loops.budget.check_cycle_budget` — pre-flight caps
   - `coco_loops.verifier.verify_proposal` — separate adversary before commit
   - `coco_loops.run_log.RunLog` — append-only audit + heartbeat
4. **CLI** — `coco-loops list`, `coco-loops status coding-loop`, `coco-loops promote coding-loop`.

## Applying the patch

The patch is **review-only** in this example tree. Apply it to your consumer daemon script after adapting paths:

```bash
# From your consumer repo root:
patch -p0 < path/to/coding-loop.phase1.patch
```

Coordinate with loop owners before enabling on a live daemon. See [docs/DEPLOY_RUNBOOK.md](../docs/DEPLOY_RUNBOOK.md) for generic deploy steps.
