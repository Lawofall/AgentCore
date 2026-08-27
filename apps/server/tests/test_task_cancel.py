"""Cancel is terminal for outbound I/O: unwrap, abort, never retry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from agentcore.core.net import abort_httpx_response
from agentcore.core.task_cancel import (
    cancel_reason_from_done_task,
    cancel_task,
    is_task_cancelled,
    raise_if_task_cancelled,
    task_is_cancelling,
)


def _http_error_from_cancel() -> httpx.ConnectError:
    req = httpx.Request("POST", "http://example.invalid/v1/chat/completions")
    err = httpx.ConnectError("wrapped-cancel", request=req)
    err.__cause__ = asyncio.CancelledError()
    return err


def test_is_task_cancelled_bare_and_wrapped():
    assert is_task_cancelled(asyncio.CancelledError()) is True
    assert is_task_cancelled(_http_error_from_cancel()) is True
    assert is_task_cancelled(httpx.ConnectError("plain")) is False


def test_raise_if_task_cancelled_unwraps_http_error():
    with pytest.raises(asyncio.CancelledError):
        raise_if_task_cancelled(_http_error_from_cancel())


def test_raise_if_task_cancelled_ignores_plain_error():
    raise_if_task_cancelled(httpx.ConnectError("plain"))


def test_task_is_cancelling_without_running_loop():
    assert task_is_cancelling() is False


async def test_cancel_task_roundtrips_reason_on_done_task():
    started = asyncio.Event()

    async def body() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(body())
    await started.wait()
    cancel_task(task, "user_stop")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_reason_from_done_task(task) == "user_stop"


async def test_raise_if_task_cancelled_while_handling_cancel():
    started = asyncio.Event()

    async def body() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert task_is_cancelling() is True
            raise_if_task_cancelled()

    task = asyncio.create_task(body())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_abort_httpx_response_closes_and_swallows_aclose_errors():
    resp = AsyncMock()
    resp.aclose = AsyncMock()
    await abort_httpx_response(resp)
    resp.aclose.assert_awaited_once()

    await abort_httpx_response(None)

    boom = AsyncMock()
    boom.aclose = AsyncMock(side_effect=RuntimeError("already closed"))
    await abort_httpx_response(boom)
