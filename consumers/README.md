# Consumers

Worked adapters that put a face on the `coco-loops` framework. The core package stays
stdlib-only and governance-only; anything that needs a model, a git working tree, or an
extra dependency lives here.

| Consumer | What it is |
|----------|------------|
| [claude-code/](claude-code/) | the `/coco-loop` skill: a plain-language goal becomes a governed, propose-only loop |
| [mcp/](mcp/) | an MCP server exposing the loop verbs, so the same flow works in Cursor and other MCP clients |
| [../examples/coding-loop/](../examples/coding-loop/) | CoCo's coding-loop adapter (customer #1): the contract + the live integration patch |

All three funnel through one governance implementation (`coco-loops` and the
`coco_loops` package), so the constitution floor is enforced in exactly one place.
