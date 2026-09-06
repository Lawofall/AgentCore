"""Unix sandboxd client: missing socket is fail-closed."""

import asyncio
import json

import pytest

from agentcore.tools.sandbox.sandboxd.client import (
    UnixSandboxdClient,
    _read_wait_stream,
)
from agentcore.tools.sandbox.sandboxd.errors import SandboxdUnavailableError


async def test_unix_client_missing_socket_is_unavailable(tmp_path):
    client = UnixSandboxdClient(str(tmp_path / "no-such.sock"))
    with pytest.raises(SandboxdUnavailableError):
        await client.ping()


async def _feed_wait_stream(*events: dict) -> tuple[int, str, str]:
    reader = asyncio.StreamReader()
    for event in events:
        reader.feed_data(json.dumps(event, separators=(",", ":")).encode() + b"\n")
    reader.feed_eof()
    return await _read_wait_stream(
        reader,
        timeout_seconds=1.0,
        on_output=None,
        stdout_buf=[],
        stderr_buf=[],
    )


async def test_read_wait_stream_preserves_exit_code_zero():
    code, stdout, stderr = await _feed_wait_stream(
        {"event": "stdout", "data": "ok"},
        {"event": "exit", "code": 0},
    )
    assert code == 0
    assert stdout == "ok"
    assert stderr == ""


async def test_read_wait_stream_preserves_nonzero_exit_code():
    code, stdout, _stderr = await _feed_wait_stream(
        {"event": "exit", "code": 127},
    )
    assert code == 127
    assert stdout == ""


async def test_read_wait_stream_missing_exit_code_defaults_to_one():
    code, _stdout, _stderr = await _feed_wait_stream({"event": "exit"})
    assert code == 1
