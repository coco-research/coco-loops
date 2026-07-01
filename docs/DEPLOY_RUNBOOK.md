# Loop Framework — Deploy Runbook

*Generic attended-deploy sequence for wiring a consumer loop to `coco-loops`. See [examples/coding-loop/](../examples/coding-loop/) for the CoCo coding-loop concrete case.*

## What ships in this package

- `specs/` — framework specs: constitution (Layer 0), constraints (Φ), budget (Π), run-log schema (E)
- `coco_loops/` — stdlib-only Python modules: verifier (A), promotion gate, budget, run-log, supervisor, CLI
- `examples/` — worked consumer adapters

## Before deploy

1. **Choose the target loop** — any daemon or scheduled agent that can call Python imports.
2. **Write a contract** — copy [specs/loop-contract.template.md](../specs/loop-contract.template.md), fill placeholders, save as `loops/<loop-id>.contract.md`.
3. **Add budget row** — edit `loops/loop-budget.md` with caps for your loop id.

## Attended deploy sequence

0. **Snapshot** any databases or state the loop will write to.
1. **Understand the loop's current behavior** — read its script/config before wrapping.
2. **Pause the loop** — `touch $COCO_LOOPS_HOME/<loop-id>.disabled` (default `~/.coco/<loop-id>.disabled`).
3. **Wire the loop to the framework:**
   - Pre-flight: `coco_loops.budget.check_cycle_budget()`
   - Constraints: read `loop-constraints.md` each cycle
   - Verify: `coco_loops.verifier.verify(...)` before any commit
   - Audit: append one run-log line per cycle via `coco_loops.run_log.RunLog`
4. **Run L1 report-only** — propose, never commit; log verifier dry-run verdicts.
5. **Start the supervisor** — `coco_loops.supervisor` watching `last_progress` heartbeat.
6. **Observe ~2 weeks** — record `human_verdict` on each proposal.
7. **Promotion gate** — `coco-loops promote <loop-id> --comprehension-signed-off` (or `python -m coco_loops.promotion_gate`). Only on PASS consider L2.

## Path configuration

| Setting | Resolution order |
|---------|------------------|
| Loops dir | `--loops-dir` → `COCO_LOOPS_DIR` → `./loops` (cwd) |
| State dir | `--state-dir` → `COCO_LOOPS_HOME` → `COCO_RUNTIME_DIR` (back-compat) → `~/.coco` |
| Run-log | `COCO_LOOP_RUN_LOG` override, else `$STATE_DIR/loop-run-log.jsonl` |

## Rollback

Everything is additive and off-switchable: remove wiring, delete `<loop-id>.disabled` to re-enable, or touch `loop-pause-all`. Framework modules are inert until a loop calls them.

## Verification

```bash
pip install -e ".[dev]"
coco-loops verify          # runs pytest suite
coco-loops list            # discovers contracts in loops dir
```
