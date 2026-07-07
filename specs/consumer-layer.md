# Consumer Layer (charter, /coco-loop, MCP)

*How a person with a vague goal gets a safe, governed loop, without learning the framework internals.*

The core `coco-loops` package is a library: a consumer wires their daemon to the
budget, verifier, run-log, and lifetime modules. That is the right shape for a
maintainer, and the wrong shape for someone who just wants to say "keep my build
green for the next four hours" and walk away. The consumer layer closes that gap.

## The idea

A person states a goal in plain language. That goal is compiled into a **charter**:
a short, readable set of principles the loop will follow, a lifetime cap, and a
maturity level. The person reads the charter, confirms it, and the loop starts,
governed and propose-only. The charter becomes both the instruction the loop obeys
and the thing we audit its behavior against. It always sits on top of the immutable
constitution floor, which no charter can override.

```
person's goal ("keep the build green for 4h")
      |
      v  (LLM compile — happens in the CONSUMER)
  Charter JSON  { loop_id, objective, principles[], guardrails[], lifetime, level: L1 }
      |
      v  coco-loops charter --from-json -   (FRAMEWORK: validate against the floor)
  human-readable charter  ->  person confirms
      |
      v  coco-loops charter --install       (FRAMEWORK: write contract + arm lifetime)
  governed loop running, propose-only, lifetime-capped
```

## The split (why it is drawn here)

The intelligence that turns a sentence into a structured charter needs a model. The
governance that decides whether a charter is safe must be dumb, pure, and auditable.
So the line is:

- **Consumer side (has a model):** the `/coco-loop` skill in Claude Code, or an MCP
  client in Cursor. It reads the person's goal, asks clarifying questions if needed,
  and produces a Charter JSON that matches the schema (`coco-loops charter --schema`).
- **Framework side (`coco_loops.charter`, stdlib only, no model, no network):** takes
  the compiled charter and validates, renders, and installs it. It never calls an LLM.
  This keeps the safety decision independent of the thing being governed, the same
  principle as maker-versus-verifier one level up.

This is why the framework stays zero-dependency: the model lives in the consumer,
never in the governance core.

## Charter schema

Produced by the consumer, validated by the framework. Run `coco-loops charter --schema`
for the authoritative copy.

| Field | Meaning | Notes |
|-------|---------|-------|
| `loop_id` | slug identifying the loop | lowercase, digits, hyphens, 1-64 chars |
| `objective` | one sentence: the bounded improvement per cycle | required (O) |
| `principles` | readable rules the loop follows | at least one; become part of Φ |
| `guardrails` | hard lines the loop never crosses | optional; extra Φ |
| `context` | where the loop reads operational state | optional (K) |
| `inputs` | queues / watchlists read each cycle | optional (I) |
| `lifetime` | active-work cap, e.g. `4h`, `90m`, `1h30m` | default `4h` |
| `level` | maturity | **L1 only at creation** |
| `budget_tokens_day` / `budget_runs_day` | optional Π overrides | positive ints |

## What the framework enforces at charter time

`coco_loops.charter.validate` rejects a charter that:

- declares a level above **L1**. New loops start propose-only; higher maturity is
  earned through the promotion gate, never self-declared (constitution I7, I8).
- has a bad slug, a missing objective, no principles, or an unparseable lifetime.
- contains text that would contract around the immutable floor. The objective,
  principles, and guardrails are scanned for intent to disable the verifier (I1),
  auto-merge or commit without approval (I4/I8), amend the constitution or denylist
  (I2/I6), send content to unsanctioned public-cloud endpoints (I3), or run
  unattended destructive / push operations (I5). Any hit is rejected with the
  invariant it violates.

A charter that fails validation never reaches disk and never arms a loop.

## CLI surface (`coco-loops charter`)

| Command | Effect |
|---------|--------|
| `coco-loops charter --schema` | print the JSON schema a consumer should fill |
| `coco-loops charter --from-json <path\|->` | validate + render for confirmation (exit 1 if invalid) |
| `coco-loops charter --from-json <path\|-> --install` | also write `<loop_id>.contract.md` + arm the lifetime + clear the kill-switch |
| `coco-loops charter --from-json <path\|-> --install --no-arm` | write the contract but do not arm the lifetime yet |

`--install` produces a discoverable contract (`coco-loops list` shows it) and an armed,
propose-only loop (`coco-loops status <loop_id>` shows the lifetime). The loop's daemon,
once wired to the framework, then runs governed.

## B1 — the `/coco-loop` skill (Claude Code)

A skill that owns the person-facing flow: take the goal, compile a charter (fill the
schema), show the rendered charter, get confirmation, then call
`coco-loops charter --install`. Named `/coco-loop` to avoid the built-in `/loop`
(an interval runner) and the `ralph-loop` plugin. The skill file ships in this repo
under `consumers/claude-code/` and is installed into `~/.claude/skills/` by an
explicit, attended step.

## B3 — the MCP server

An MCP server that exposes the same verbs (`charter`, `list`, `status`, `promote`) as
tools, so the flow works in Cursor and any MCP client, not only Claude Code. It shells
out to the same `coco-loops` CLI, so there is one implementation of the governance and
one place the floor is enforced. Ships under `consumers/mcp/`.

## Status

- **B2 charter engine:** built (`coco_loops/charter.py`, `coco-loops charter`), tested.
- **B1 skill:** pending.
- **B3 MCP server:** pending.
