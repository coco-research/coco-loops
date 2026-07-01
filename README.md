# coco-loops

**Principle-governed self-evolving loop orchestration framework** — stdlib-only Python package extracted from the CoCo loop-engineering stack. Wraps autonomous agent loops in immutable constitution, separate-adversary verification, budget caps, append-only audit, and promotion gates.

## What it is

A loop is an **agentic contract** `C = ⟨O, K, I, Y, Φ, A, Π, E⟩` under an immutable **constitution** (Layer 0). The maker never grades its own work — a **separate verifier** (`A`) holds commit authority. This package provides the mechanical governance layer; consumers register loop-specific contracts and wire their daemons to the framework modules.

## Install (local)

```bash
cd /path/to/coco-loops
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

No third-party runtime dependencies. Dev extra installs `pytest`.

## CLI verbs

```bash
coco-loops list                          # contracts, levels, pause state
coco-loops status <loop-id>              # run-log + budget for one loop
coco-loops promote <loop-id>             # L1→L2 promotion gate (PASS/FAIL)
coco-loops verify                        # run framework pytest suite
```

### Path configuration

| Flag / env | Purpose | Default |
|------------|---------|---------|
| `--loops-dir` / `COCO_LOOPS_DIR` | Contract discovery (`*.contract.md`) | `./loops` (cwd) |
| `--state-dir` / `COCO_LOOPS_HOME` | Kill-switch, run-log, budget artifacts | `~/.coco` |
| `COCO_LOOP_RUN_LOG` | Override run-log path | `$STATE_DIR/loop-run-log.jsonl` |

`COCO_RUNTIME_DIR` is accepted as a back-compat alias for `COCO_LOOPS_HOME`.

Shell shim: `bin/coco-loops` delegates to the installed console script.

## Registering a consumer loop

1. Copy [specs/loop-contract.template.md](specs/loop-contract.template.md) → `loops/<your-loop>.contract.md` and fill placeholders.
2. Add budget caps in `loops/loop-budget.md`.
3. In your daemon/script:
   - `coco_loops.budget.check_cycle_budget()` — pre-flight
   - `coco_loops.verifier.verify(change, test_runner)` — before commit
   - `coco_loops.run_log.RunLog(path).append(entry)` — audit + heartbeat
4. Optional: `coco_loops.supervisor.decide(...)` for liveness monitoring.
5. Inspect: `coco-loops list` / `coco-loops status <your-loop>`.

See [examples/coding-loop/](examples/coding-loop/) for CoCo's coding-loop adapter (customer #1).

## Package layout

```
coco_loops/          # verifier, budget, run_log, supervisor, promotion_gate, cli, paths
specs/               # constitution, constraints, budget, run-log schema, contract template
examples/            # worked consumer adapters
docs/                # deploy runbook
tests/               # framework tests
```

## Specs index

| File | Component |
|------|-----------|
| [loop-constitution.md](specs/loop-constitution.md) | Layer 0 (immutable invariants) |
| [loop-constraints.md](specs/loop-constraints.md) | Φ — pre-flight denylist + guardrails |
| [loop-budget.md](specs/loop-budget.md) | Π — caps, kill switch |
| [loop-run-log.schema.md](specs/loop-run-log.schema.md) | E — audit + heartbeat schema |
| [loop-contract.template.md](specs/loop-contract.template.md) | Reusable contract template |

## License

Internal / local use. See consumer project for distribution terms.
