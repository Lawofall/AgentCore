"""ReAct round exception fingerprint — unclassified crash must log error_type."""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import LLMTimeoutError
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage
from agentcore.runtime.engine.governance import decide_llm_failure
from agentcore.runtime.engine.round import (
    ORIGIN_STREAM_ABORTED,
    ORIGIN_STREAM_ROUND,
    LlmRoundFailure,
    run_llm_round,
)
from tests.llm_helpers import make_profile_params


class _BoomAfterContent:
    async def stream(self, request):  # noqa: ANN001 — duck-typed like stall tests
        yield LLMChunk(delta_content="hello")
        raise TypeError('can only concatenate str (not "list") to str')


class _TimeoutBeforeChunk:
    async def stream(self, request):  # noqa: ANN001
        raise LLMTimeoutError("模型流式响应停滞（长时间无输出），请稍后重试")
        yield LLMChunk()  # pragma: no cover — keep this an async generator


async def _run(llm: object) -> LlmRoundFailure | object:
    return await run_llm_round(
        llm=llm,  # type: ignore[arg-type]
        profile=make_profile_params(),
        messages=[LLMMessage(role="user", content="hi")],
        investigation_tools=frozenset(),
        tool_defs=None,
        active_model="m",
        emit_content=lambda _d: None,
        emit_reasoning=lambda _d: None,
        on_tool_progress=None,
        round_idx=0,
        run_id="r-round-exc",
        raise_on_error=False,
    )


@pytest.mark.anyio
async def test_unclassified_stream_crash_logs_error_type_and_fallback_face():
    with capture_logs() as caps:
        result = await _run(_BoomAfterContent())
    assert isinstance(result, LlmRoundFailure)
    assert result.error_code == ErrorCode.LLM_ERROR
    assert result.error_message == "出了点问题，请稍后重试。"
    assert result.classified is False
    assert result.error_type == "TypeError"
    assert result.origin == ORIGIN_STREAM_ROUND
    assert result.error_preview is not None
    assert "concatenate" in result.error_preview

    hit = next(c for c in caps if c.get("event") == "engine.llm_round_exception")
    assert hit["error_type"] == "TypeError"
    assert hit["error_code"] == ErrorCode.LLM_ERROR
    assert hit["classified"] is False
    assert hit["origin"] == ORIGIN_STREAM_ROUND
    assert "concatenate" in hit["error"]


@pytest.mark.anyio
async def test_classified_leaf_error_logs_without_generic_fallback():
    with capture_logs() as caps:
        result = await _run(_TimeoutBeforeChunk())
    assert isinstance(result, LlmRoundFailure)
    assert result.error_code == ErrorCode.LLM_TIMEOUT
    assert result.classified is True
    assert result.error_type == "LLMTimeoutError"
    assert "出了点问题" not in result.error_message

    hit = next(c for c in caps if c.get("event") == "engine.llm_round_exception")
    assert hit["classified"] is True
    assert hit["error_type"] == "LLMTimeoutError"


def test_decide_llm_failure_terminal_carries_round_fingerprint():
    with capture_logs() as caps:
        decide_llm_failure(
            final_content="",
            error_code=ErrorCode.LLM_ERROR,
            role="captain",
            error_type="TypeError",
            origin=ORIGIN_STREAM_ROUND,
            classified=False,
            error='can only concatenate str (not "list") to str',
        )
    terminal = next(c for c in caps if c.get("event") == "engine.llm_failed_terminal")
    assert terminal["reason"] == "error"
    assert terminal["has_content"] is False
    assert terminal["error_type"] == "TypeError"
    assert terminal["origin"] == ORIGIN_STREAM_ROUND
    assert terminal["classified"] is False
    assert terminal["error_code"] == ErrorCode.LLM_ERROR


def test_decide_llm_failure_aborted_origin():
    with capture_logs() as caps:
        decide_llm_failure(
            final_content="partial",
            error_code=ErrorCode.LLM_ERROR,
            role="captain",
            origin=ORIGIN_STREAM_ABORTED,
        )
    terminal = next(c for c in caps if c.get("event") == "engine.llm_failed_terminal")
    assert terminal["origin"] == ORIGIN_STREAM_ABORTED
    assert terminal["has_content"] is True
    assert "error_type" not in terminal
