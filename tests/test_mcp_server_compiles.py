"""The MCP consumer server is syntax-checked here (its `mcp` dep is not installed
in the core test env, so this compiles it rather than importing it)."""

from __future__ import annotations

import py_compile
from pathlib import Path


def test_mcp_server_compiles():
    server = Path(__file__).resolve().parents[1] / "consumers" / "mcp" / "server.py"
    assert server.exists(), server
    py_compile.compile(str(server), doraise=True)
