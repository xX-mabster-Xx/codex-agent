import asyncio
import json
from types import SimpleNamespace

import pytest

from codex_client import CodexClient


@pytest.mark.asyncio
async def test_stdout_reader_accepts_large_json_rpc_notification():
    """A large MCP item must not trip asyncio's 64 KiB readline limit."""
    stream = asyncio.StreamReader(limit=64)
    stream.feed_data(
        json.dumps(
            {
                "method": "item/completed",
                "params": {"tool_output": "x" * (256 * 1024)},
            }
        ).encode()
        + b"\n"
    )
    stream.feed_eof()

    client = CodexClient.__new__(CodexClient)
    process = SimpleNamespace(stdout=stream, returncode=0)
    client._process = process
    client._pending = {}
    client._item_snapshots = {}
    client.events = asyncio.Queue()
    client.server_requests = asyncio.Queue()
    client._recovery_task = None

    await client._read_stdout(process)

    method, params = client.events.get_nowait()
    assert method == "item/completed"
    assert len(params["tool_output"]) == 256 * 1024
