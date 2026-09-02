#!/usr/bin/env python3
"""MCP client for the local root executor with Telegram approval."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastmcp import FastMCP


SOCKET_PATH = "/run/codex-sudo/daemon.sock"
MAX_RESPONSE_BYTES = 140_000
APPROVAL_TIMEOUT_SECONDS = 600

mcp = FastMCP(
    "sudo",
    instructions=(
        "Runs exactly one shell command as root through a local Unix socket. "
        "Every request is shown in the separate root-approval Telegram bot and "
        "will execute only if the user presses Approve there. Always provide the "
        "exact command, working directory, reason, expected result, and material risks."
    ),
)


async def call_daemon(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(SOCKET_PATH), timeout=5
        )
    except OSError as error:
        return {
            "ok": False,
            "error": (
                f"Root executor is unavailable at {SOCKET_PATH}: {error}. "
                "Install and start codex-sudo.service first."
            ),
        }
    try:
        writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
        response_timeout = payload["timeout_seconds"] + APPROVAL_TIMEOUT_SECONDS + 20
        raw = await asyncio.wait_for(reader.readline(), timeout=response_timeout)
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            return {
                "ok": False,
                "error": "Root executor returned no response or a too-large response",
            }
        response = json.loads(raw)
        return (
            response
            if isinstance(response, dict)
            else {"ok": False, "error": "Invalid executor response"}
        )
    except (OSError, TimeoutError, json.JSONDecodeError) as error:
        return {"ok": False, "error": f"Root executor communication failed: {error}"}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


@mcp.tool(
    name="run_as_root",
    title="Request root command",
    description=(
        "Request execution of exactly one root command on this local machine. "
        "The command does not start until it is approved in the dedicated "
        "root-approval Telegram bot."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def run_as_root(
    command: str,
    cwd: str,
    reason: str,
    expected_result: str,
    risks: str,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Queue a root command and wait for the Telegram approval decision."""
    return await call_daemon(
        {
            "action": "submit",
            "command": command,
            "cwd": cwd,
            "reason": reason,
            "expected_result": expected_result,
            "risks": risks,
            "timeout_seconds": timeout_seconds,
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
