# MCP consumer: drive coco-loops from Cursor and other MCP clients

`server.py` is an MCP server that exposes the coco-loops verbs as tools, so a governed
loop can be driven from Cursor, Claude Desktop, or any MCP client. Each tool shells out
to the `coco-loops` CLI, so there is one implementation of the governance and one place
the constitution floor is enforced.

## Tools

| Tool | Effect |
|------|--------|
| `charter_schema` | the JSON schema a charter must fill |
| `charter_preview` | validate a charter against the floor + render it (no install) |
| `charter_install` | validate, write the contract, arm the lifetime (propose-only) |
| `loop_list` | list contracts, levels, pause state |
| `loop_status` | recent proposals, verdicts, lifetime usage for a loop |
| `loop_start` | arm a loop's lifetime cap + clear the kill-switch |
| `loop_promote` | run the L1 to L2 promotion gate |
| `loop_run` | run a chartered loop propose-only in a clean worktree |

The client-side flow mirrors the `/coco-loop` skill: compile the goal into a charter,
`charter_preview` it, confirm with the user, `charter_install`, seed a JSON task queue,
then `loop_run` against a dedicated clean worktree.

## Install

```bash
pip install "mcp[cli]"        # the MCP SDK
pip install -e /path/to/coco-loops   # so coco-loops is on PATH
```

### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "coco-loops": {
      "command": "python",
      "args": ["/absolute/path/to/coco-loops/consumers/mcp/server.py"]
    }
  }
}
```

### Claude Desktop

Add the same block to the `mcpServers` object in the Claude Desktop config, then restart.

## Testing note

The core `coco-loops` package is stdlib-only and fully covered by the framework test
suite. This MCP server is thin wiring over the tested CLI and adds one runtime
dependency (`mcp`), which is why it lives here under `consumers/` and not in the core
package. It is validated for syntax in CI, and its behavior is the CLI's behavior.
