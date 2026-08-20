"""Soft-fail settle: error SSE must land on turn_end + settle result."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcore.core.error_codes import ErrorCode
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    delivery_status,
    error_event,
    tool_use_end,
)
from agentcore.runtime.pipeline.finalize import _journal_entries_for_turn
from agentcore.runtime.pipeline.resume.finish import finish_resume_turn
from agentcore.runtime.pipeline.settle import (
    salvage_pipeline_exception,
    settle_successful_turn,
)
from tests.llm_helpers import make_profile_params


@pytest.mark.asyncio
async def test_settle_soft_fail_persists_error_on_result_and_journal():
    sink = EventSink()
    sink.emit(
        error_event(
            ErrorCode.LLM_TIMEOUT,
            "连接 byok 超时，请检查网络后重试",
        )
    )

    captain_state = SimpleNamespace(
        content="",
        reasoning="",
        rounds=1,
        usage=TokenUsage().as_dict(),
        cost={"total": 0, "currency": "USD"},
        model="deepseek-v4-flash",
        duration_ms=0,
        finish_override=FinishReason.ERROR,
    )
    delegate = SimpleNamespace(
        usage={},
        run_ledger=[],
        citations=[],
        collab={"boundary_yields": 0, "scope_signals": 0, "escalations": 0},
        continuation_count=0,
        user_continuation_count=0,
        dispose_open_supervised=AsyncMock(),
    )
    debate = SimpleNamespace(usage={}, run_ledger=[], citations=[])
    profile = SimpleNamespace(max_rounds=20)
    audit = SimpleNamespace(drops=0, flush=AsyncMock())
    journal_writer = SimpleNamespace(flush=AsyncMock())

    result = await settle_successful_turn(
        message_id="m1",
        captain_run_id="cap",
        captain_state=captain_state,
        delegate_tool=delegate,
        debate_tool=debate,
        profile=profile,
        citations=[],
        vision_cost_sink=[],
        sink=sink,
        fact_log=None,
        audit_recorder=audit,
        roster_writer=None,
        journal_writer=journal_writer,
    )

    assert result["finish_reason"] is FinishReason.ERROR
    assert result["error_code"] == ErrorCode.LLM_TIMEOUT
    assert "超时" in result["error"]
    entries = result["journal_entries"]
    assert entries is not None
    turn_end = next(e for e in entries if e["kind"] == "turn_end")
    assert turn_end["payload"]["finish_reason"] == "error"
    assert turn_end["payload"]["error"]["code"] == ErrorCode.LLM_TIMEOUT


@pytest.mark.asyncio
async def test_finish_resume_soft_fail_stamps_last_turn_error():
    """L1 方案 2：resume finish → settle 同核，soft-fail last_turn_error 须上结果。"""
    sink = EventSink()
    sink.emit(error_event(ErrorCode.LLM_TIMEOUT, "resume soft-fail 超时"))

    captain_state = SimpleNamespace(
        content="续跑正文",
        reasoning="",
        rounds=1,
        usage=TokenUsage().as_dict(),
        cost={"total": 0, "currency": "USD"},
        model="m",
        duration_ms=0,
        finish_override=FinishReason.ERROR,
    )
    result = await finish_resume_turn(
        message_id="m-resume",
        captain_run_id="cap",
        captain_state=captain_state,
        pre_pause_content="挂起前",
        delegate_tool=SimpleNamespace(
            usage={},
            run_ledger=[],
            citations=[],
            collab={},
            continuation_count=0,
            user_continuation_count=0,
            dispose_open_supervised=AsyncMock(),
        ),
        debate_tool=SimpleNamespace(usage={}, run_ledger=[], citations=[]),
        profile=make_profile_params(max_rounds=20),
        citations=[],
        sink=sink,
        fact_log=None,
        audit_recorder=SimpleNamespace(drops=0, flush=AsyncMock()),
        roster_writer=None,
        journal_writer=SimpleNamespace(flush=AsyncMock()),
    )
    assert result["error_code"] == ErrorCode.LLM_TIMEOUT
    assert "超时" in result["error"]
    assert "挂起前" in result["content"]
    assert "续跑正文" in result["content"]


@pytest.mark.asyncio
async def test_salvage_pipeline_exception_carries_journal_entries():
    """Exception salvage must assemble journal (resume path reuses this kernel)."""
    sink = EventSink()
    sink._process.append({"kind": "reasoning", "text": "partial"})
    audit = SimpleNamespace(drops=0, flush=AsyncMock())

    result = await salvage_pipeline_exception(
        e=RuntimeError("boom mid-resume"),
        message_id="m1",
        sink=sink,
        fact_log=None,
        audit_recorder=audit,
        roster_writer=None,
    )
    assert result["finish_reason"] is FinishReason.ERROR
    assert result["error_code"] == ErrorCode.PIPELINE_ERROR
    assert result["journal_entries"] is not None
    turn_end = next(e for e in result["journal_entries"] if e["kind"] == "turn_end")
    assert turn_end["payload"]["finish_reason"] == "error"


@pytest.mark.asyncio
async def test_salvage_pipeline_exception_hides_raw_exception_from_user_face():
    """Unclassified exceptions: product message on settle result / SSE, not str(e)."""
    from agentcore.core.errors import UNCLASSIFIED_EXCEPTION_USER_MESSAGE

    sink = EventSink()
    audit = SimpleNamespace(drops=0, flush=AsyncMock())
    raw = "build_turn_router requires explicit credentials (no silent platform key)"

    result = await salvage_pipeline_exception(
        e=RuntimeError(raw),
        message_id="m-leak",
        sink=sink,
        fact_log=None,
        audit_recorder=audit,
        roster_writer=None,
    )
    assert result["error_code"] == ErrorCode.PIPELINE_ERROR
    assert result["error"] == UNCLASSIFIED_EXCEPTION_USER_MESSAGE
    assert "build_turn_router" not in result["error"]
    turn_err = sink.last_turn_error()
    assert turn_err is not None
    assert turn_err.get("message") == UNCLASSIFIED_EXCEPTION_USER_MESSAGE


@pytest.mark.asyncio
async def test_salvage_pipeline_exception_preserves_agentcore_product_copy():
    """AgentCoreError with curated zh must not be replaced by the unclassified default."""
    from agentcore.core.errors import UNCLASSIFIED_EXCEPTION_USER_MESSAGE, LLMAuthError

    sink = EventSink()
    audit = SimpleNamespace(drops=0, flush=AsyncMock())
    exc = LLMAuthError()

    result = await salvage_pipeline_exception(
        e=exc,
        message_id="m-auth",
        sink=sink,
        fact_log=None,
        audit_recorder=audit,
        roster_writer=None,
    )
    assert result["error_code"] == ErrorCode.LLM_KEY_INVALID
    assert result["error"] == exc.message
    assert "无效" in result["error"]
    assert result["error"] != UNCLASSIFIED_EXCEPTION_USER_MESSAGE


@pytest.mark.asyncio
async def test_salvage_missing_llm_credentials_uses_settings_guidance():
    """MissingLLMCredentialsError is AgentCoreError — product face asks to re-pick a model."""
    from agentcore.llm.factory import MissingLLMCredentialsError

    sink = EventSink()
    audit = SimpleNamespace(drops=0, flush=AsyncMock())
    exc = MissingLLMCredentialsError(
        "build_turn_router requires explicit credentials (no silent platform key)"
    )

    result = await salvage_pipeline_exception(
        e=exc,
        message_id="m-creds",
        sink=sink,
        fact_log=None,
        audit_recorder=audit,
        roster_writer=None,
    )
    assert result["error_code"] == ErrorCode.VALIDATION_ERROR
    assert "改选可用模型" in result["error"]
    assert "设置" not in result["error"]
    assert "build_turn_router" not in result["error"]
    assert exc.details.get("invariant", "").startswith("build_turn_router")


def test_journal_entries_include_error_when_process_present():
    sink = EventSink()
    # Simulate a process step so the journal gate opens for reasons other than error.
    sink._process.append({"kind": "reasoning", "text": "…" })
    sink.emit(error_event(ErrorCode.LLM_KEY_INVALID, "Key 无效"))

    entries = _journal_entries_for_turn(None, sink=sink, finish=FinishReason.ERROR)
    assert entries is not None
    turn_end = next(e for e in entries if e["kind"] == "turn_end")
    assert turn_end["payload"]["error"]["code"] == ErrorCode.LLM_KEY_INVALID


@pytest.mark.asyncio
async def test_settle_salvages_delegate_reply_as_partial():
    """Worker 已落盘 + CEO 汇总限流：回合 partial，回复取 delegate 交代。"""
    sink = EventSink()
    debrief = "已落盘 订单.csv、明细.csv、汇总.csv。数据分析队员因上游限流失败。"
    sink.emit(
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output=debrief,
            partial_failure=True,
        )
    )
    sink.emit(
        delivery_status(
            execution_id="exec_rl",
            state="partial",
            summary="已交付 3 个文件；1 项未完成",
            delivered_files=["订单.csv", "明细.csv", "汇总.csv"],
            gaps=[{"role": "数据分析", "description": "限流", "reason": "node_failed"}],
            actions=[],
        )
    )
    sink.emit(error_event(ErrorCode.LLM_RATE_LIMIT, "上游限流，请约 4 秒后再试"))

    captain_state = SimpleNamespace(
        content="",
        reasoning="",
        rounds=2,
        usage=TokenUsage().as_dict(),
        cost={"total": 0, "currency": "USD"},
        model="deepseek-v4-flash",
        duration_ms=0,
        finish_override=FinishReason.ERROR,
    )
    result = await settle_successful_turn(
        message_id="m-partial",
        captain_run_id="cap",
        captain_state=captain_state,
        delegate_tool=SimpleNamespace(
            usage={},
            run_ledger=[],
            citations=[],
            collab={"boundary_yields": 0, "scope_signals": 0, "escalations": 0},
            continuation_count=0,
            user_continuation_count=0,
            dispose_open_supervised=AsyncMock(),
        ),
        debate_tool=SimpleNamespace(usage={}, run_ledger=[], citations=[]),
        profile=SimpleNamespace(max_rounds=20),
        citations=[],
        vision_cost_sink=[],
        sink=sink,
        fact_log=None,
        audit_recorder=SimpleNamespace(drops=0, flush=AsyncMock()),
        roster_writer=None,
        journal_writer=SimpleNamespace(flush=AsyncMock()),
    )

    assert result["outcome"] == "partial"
    assert result["content"] == debrief
    assert result["error_code"] == ErrorCode.LLM_RATE_LIMIT
    turn_end = next(e for e in result["journal_entries"] if e["kind"] == "turn_end")
    assert turn_end["payload"]["outcome"] == "partial"
    assert any(
        getattr(e.type, "value", e.type) == "content_delta"
        and (e.payload or {}).get("delta") == debrief
        for e in sink.history_snapshot()
    )


@pytest.mark.asyncio
async def test_settle_refuses_ceo_audience_coordination_echo_on_degraded_rate_limit():
    """0538c624 shape: empty captain + coordination echo + degraded 429.

    Salvage must not copy the host echo into the user bubble. The turn still
    carries LLM_RATE_LIMIT so the client paints the rate-limit face (not an
    empty hidden bubble — hideEmptyBubble is cancel-only).
    """
    from agentcore.tools.protocol import TOOL_AUDIENCE_CEO

    echo = (
        "【团队已启动·协调模式】已派出 2 名队员（调研、写手）；图共 2 名，其中 0 名已完成。\n"
        "调度在后台继续；完成态由图事件异步呈现。\n"
        "你将收到团队事件（worker_completed / note / escalation / "
        "user_interjection / all_completed）。无需处置时调 wait（或空响应，系统已豁免）——"
        "派完若结束本回合：可见正文只留一句短的「人已派出」。"
    )
    sink = EventSink()
    sink.emit(
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output=echo,
            audience=TOOL_AUDIENCE_CEO,
        )
    )
    sink.emit(error_event(ErrorCode.LLM_RATE_LIMIT, "上游限流，请约 4 秒后再试"))

    captain_state = SimpleNamespace(
        content="",
        reasoning="",
        rounds=2,
        usage=TokenUsage().as_dict(),
        cost={"total": 0, "currency": "USD"},
        model="deepseek-v4-flash",
        duration_ms=0,
        finish_override=FinishReason.DEGRADED,
    )
    result = await settle_successful_turn(
        message_id="m-coord-rl",
        captain_run_id="cap",
        captain_state=captain_state,
        delegate_tool=SimpleNamespace(
            usage={},
            run_ledger=[],
            citations=[],
            collab={"boundary_yields": 0, "scope_signals": 0, "escalations": 0},
            continuation_count=0,
            user_continuation_count=0,
            dispose_open_supervised=AsyncMock(),
        ),
        debate_tool=SimpleNamespace(usage={}, run_ledger=[], citations=[]),
        profile=SimpleNamespace(max_rounds=20),
        citations=[],
        vision_cost_sink=[],
        sink=sink,
        fact_log=None,
        audit_recorder=SimpleNamespace(drops=0, flush=AsyncMock()),
        roster_writer=None,
        journal_writer=SimpleNamespace(flush=AsyncMock()),
    )

    assert result["content"] == ""
    assert result["finish_reason"] is FinishReason.DEGRADED
    assert result["error_code"] == ErrorCode.LLM_RATE_LIMIT
    assert "系统已豁免" not in (result.get("error") or "")
    assert not any(
        getattr(e.type, "value", e.type) == "content_delta"
        and "系统已豁免" in str((e.payload or {}).get("delta") or "")
        for e in sink.history_snapshot()
    )
