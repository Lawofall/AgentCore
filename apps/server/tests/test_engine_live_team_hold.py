"""队未散时主管无工具收口必须听团，不能把用户回合标成结束。"""

from __future__ import annotations

from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.graph.session.session import (
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)
from agentcore.runtime.engine.directive import Continue, Return
from agentcore.runtime.engine.outcome import RoundOutcome
from agentcore.runtime.engine.round import decide_no_tool_round
from agentcore.runtime.events import FinishReason
from agentcore.runtime.loop_controller import LoopController


def _controller() -> LoopController:
    return LoopController(
        tool_failure_warn=3,
        tool_failure_disable=5,
        unproductive_threshold=3,
        convergence_finalize_rounds=3,
        investigation_tools=frozenset(),
    )


def _decide(content: str, *, role: str = "captain"):
    return decide_no_tool_round(
        RoundOutcome(content=content, reasoning="", usage=TokenUsage()),
        final_content=content,
        controller=_controller(),
        annotate_citations=False,
        citation_sink=None,
        finish_guard_reworks=0,
        role=role,
    )


def test_captain_content_continues_while_team_live():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-hold",
        total_workers=2,
        conversation_id="c-hold",
    )
    set_active_coordination(session)
    try:
        directive = _decide("通道还是差，我换打法。")
        assert isinstance(directive, Continue)
    finally:
        clear_active_coordination()


def test_captain_empty_continues_while_team_live():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-hold-empty",
        total_workers=1,
        conversation_id="c-hold-empty",
    )
    set_active_coordination(session)
    try:
        directive = _decide("")
        assert isinstance(directive, Continue)
    finally:
        clear_active_coordination()


def test_captain_length_empty_does_not_hold_while_team_live():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-hold-length",
        total_workers=1,
        conversation_id="c-hold-length",
    )
    set_active_coordination(session)
    try:
        directive = decide_no_tool_round(
            RoundOutcome(
                content="",
                reasoning="",
                usage=TokenUsage(),
                finish_reason="length",
            ),
            final_content="",
            controller=_controller(),
            annotate_citations=False,
            citation_sink=None,
            finish_guard_reworks=0,
            role="captain",
        )
        assert isinstance(directive, Return)
        assert directive.finish_reason is FinishReason.DEGRADED
    finally:
        clear_active_coordination()


def test_worker_may_return_while_parent_team_live():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-worker",
        total_workers=2,
        conversation_id="c-worker",
    )
    set_active_coordination(session)
    try:
        directive = _decide("队员交卷。", role="worker")
        assert isinstance(directive, Return)
    finally:
        clear_active_coordination()


def test_captain_returns_after_session_closes():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-closed",
        total_workers=1,
        conversation_id="c-closed",
    )
    set_active_coordination(session)
    session.close()
    try:
        directive = _decide("终稿在此。")
        assert isinstance(directive, Return)
    finally:
        clear_active_coordination()


def test_captain_returns_without_coordination():
    clear_active_coordination()
    directive = _decide("闲聊一句。")
    assert isinstance(directive, Return)
