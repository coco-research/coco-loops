"""coco-loops MCP server.

Exposes the coco-loops verbs as MCP tools so a governed loop can be driven from
Cursor, Claude Desktop, or any MCP client, not only from Claude Code. Every tool
shells out to the `coco-loops` CLI, so there is exactly one implementation of the
governance and one place the constitution floor is enforced.

Run:   python consumers/mcp/server.py
Deps:  pip install "mcp[cli]"   and coco-loops on PATH (pip install -e the repo).
"""
from __future__ import annotations

import shutil
import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("coco-loops")


def _run(args: list[str], *, stdin: str | None = None, timeout: int = 120) -> str:
    exe = shutil.which("coco-loops")
    if not exe:
        return "error: coco-loops is not on PATH (pip install -e the coco-loops repo)"
    proc = subprocess.run(
        [exe, *args], input=stdin, capture_output=True, text=True, timeout=timeout
    )
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return out or f"(exit {proc.returncode}, no output)"


@mcp.tool()
def charter_schema() -> str:
    """Return the JSON schema a charter must fill to turn a goal into a governed loop."""
    return _run(["charter", "--schema"])


@mcp.tool()
def charter_preview(charter_json: str) -> str:
    """Validate a charter against the constitution floor and render it for confirmation.

    Does not install or arm anything. Returns the readable charter, or the validation
    errors (with the invariant each one violates) if it is rejected.
    """
    return _run(["charter", "--from-json", "-"], stdin=charter_json)


@mcp.tool()
def charter_install(charter_json: str) -> str:
    """Validate, write the contract, and arm the lifetime (propose-only).

    Confirm the rendered charter with the user (via charter_preview) before calling this.
    """
    return _run(["charter", "--from-json", "-", "--install"], stdin=charter_json)


@mcp.tool()
def loop_list() -> str:
    """List known loop contracts, their maturity levels, and pause state."""
    return _run(["list"])


@mcp.tool()
def loop_status(loop_id: str) -> str:
    """Show recent run-log proposals, verifier verdicts, and lifetime usage for a loop."""
    return _run(["status", loop_id])


@mcp.tool()
def loop_start(loop_id: str, duration: str = "4h") -> str:
    """Arm a loop's active-work lifetime cap and clear its kill-switch."""
    return _run(["start", loop_id, "--for", duration])


@mcp.tool()
def loop_promote(loop_id: str, comprehension_signed_off: bool = False) -> str:
    """Run the L1 to L2 promotion gate for a loop (PASS or FAIL)."""
    args = ["promote", loop_id]
    if comprehension_signed_off:
        args.append("--comprehension-signed-off")
    return _run(args)


@mcp.tool()
def loop_run(
    loop_id: str,
    repo: str,
    tasks: str,
    once: bool = True,
    model: str = "composer-2.5",
) -> str:
    """Run a chartered loop propose-only in a clean worktree, reading tasks from a JSON queue.

    `repo` should be a dedicated, clean worktree (the loop reverts its own changes each
    cycle and refuses to run on a dirty tree). `once` runs a single cycle.
    """
    args = ["run", loop_id, "--repo", repo, "--tasks", tasks, "--model", model]
    if once:
        args.append("--once")
    return _run(args, timeout=1200)


if __name__ == "__main__":
    mcp.run()
