"""Regression: long-lived SSE routes must return the request DB session before streaming.

FastAPI keeps ``Depends(get_db)`` open until the response body finishes. For SSE that
is the whole stream — these tests lock the shared ``release_request_db_before_sse``
call so removing it turns the suite red.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from agentcore.api.routes import realtime as realtime_mod
from agentcore.api.routes.conversations import messages as messages_mod
from agentcore.api.routes.inference import proxy as inference_proxy
from agentcore.llm.provider.protocol import LLMChunk, LLMRequest, TokenUsage
from agentcore.llm.resolve import ModelConfig
from agentcore.runtime.events import EventSink
from agentcore.runtime.turn.runs import turn_runs

pytestmark = pytest.mark.anyio


def _tracking_session() -> tuple[AsyncMock, list[str]]:
    order: list[str] = []
    session = AsyncMock()

    async def _close() -> None:
        order.append("close")

    session.close = _close
    return session, order


async def test_realtime_releases_db_before_first_frame():
    """GET /v1/realtime must close the request session before the ready frame."""
    session, order = _tracking_session()
    user = SimpleNamespace(user_id="u-realtime")

    resp = await realtime_mod.realtime_firehose(user=user, session=session)
    assert order == ["close"], "session must be returned before StreamingResponse"

    body = resp.body_iterator
    first = await body.__anext__()
    assert "ready" in first
    await body.aclose()


async def test_attach_stream_releases_db_before_sse():
    """GET …/stream attach path releases after ownership, before attach SSE."""
    session, order = _tracking_session()
    user = SimpleNamespace(user_id="u-attach")
    conv_id = "conv-attach-release"
    conv_repo = AsyncMock()
    conv_repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=conv_id))

    sink = EventSink()
    hang = asyncio.Event()

    async def _never() -> None:
        await hang.wait()

    task = asyncio.create_task(_never())
    turn_runs.register(conversation_id=conv_id, task=task, sink=sink)
    try:
        resp = await messages_mod.attach_stream(
            conversation_id=conv_id,
            user=user,
            session=session,
            conv_repo=conv_repo,
            last_event_id=None,
        )
        assert order == ["close"]
        assert resp.media_type == "text/event-stream"
        await resp.body_iterator.aclose()
    finally:
        hang.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)


async def test_inference_stream_releases_db_before_first_upstream_token(monkeypatch):
    """Streaming inference closes the session before waiting on the model stream."""
    session, order = _tracking_session()
    user = SimpleNamespace(user_id="u-inf")
    cost_repo = AsyncMock()

    async def _resolve(*_a, **_k) -> ModelConfig:
        order.append("resolve")
        return ModelConfig(
            model="mock-model",
            base_url="http://mock",
            api_key="sk",
            source="byok",
            purpose="chat",
        )

    monkeypatch.setattr(inference_proxy, "_resolve_inference_credentials", _resolve)
    monkeypatch.setattr(
        inference_proxy, "enforce_inference_proxy_rate_limit", AsyncMock()
    )

    class _Provider:
        def stream(self, _request: LLMRequest):
            async def _gen():
                order.append("first_token")
                yield LLMChunk(
                    delta_content="hi",
                    finish_reason="stop",
                    usage=TokenUsage(input_tokens=1, output_tokens=1),
                )

            return _gen()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(inference_proxy, "build_provider", lambda _creds: _Provider())
    monkeypatch.setattr(inference_proxy, "_record_proxy_spend", AsyncMock())
    monkeypatch.setattr(
        "agentcore.llm.credentials.bind_credential_pricing_context",
        lambda *_a, **_k: None,
    )

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/inference/v1/chat/completions",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)

    async def _json() -> dict:
        return {
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
            "model": "mock-model",
        }

    request.json = _json  # type: ignore[method-assign]

    resp = await inference_proxy.inference_chat_completions(
        request=request,
        user=user,
        session=session,
        cost_repo=cost_repo,
    )
    # _forward_stream peeks the first upstream chunk before returning; close must
    # precede that peek so the pool is free during model generation.
    assert order[:2] == ["resolve", "close"]
    assert "first_token" in order
    assert order.index("close") < order.index("first_token")
    assert resp.media_type == "text/event-stream"
    await resp.body_iterator.aclose()
