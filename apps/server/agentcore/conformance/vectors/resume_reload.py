"""Conformance vectors — resumed reload == live (turn_paused batch 8).

Locks deliverable continuity across plan_review resume, G6 content_reset
reinjection of pre_pause, and ask_user absorb (``content_reset(reason=ask_user)``
clears the bubble; card carries the question). Reinjection is encoded as an
ordinary ``content_delta`` so the oracle needs no special case.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    checkpoint_required,
    checkpoint_resolved,
    content_delta,
    content_reset,
    message_end,
    message_start,
    plan_review_required,
    plan_review_resolved,
    run_completed,
    run_plan,
    run_started,
)

from ._common import _CONV, _COST, _USAGE

# Shared pre_pause base for continuity + G6 reinject (G6 appends "\n\n").
_PRE_PAUSE = "阶段成果如下。"
_POST_RESUME = "按复核结论继续交付。"
_DRAFT_DISCARDED = "这一版将被核验回炉丢弃。"
_REWRITE = "重写后的交付正文。"
_ABSORB_PROSE = "帮你分析一下选项："
_ABSORB_CONTINUE = "收到，继续推进交付。"


def _plan_review_agents_and_runs() -> tuple[list[dict], list[dict]]:
    agents = [
        {
            "id": "w1",
            "role": "调研",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "执行",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "出方案", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "落地", "depends_on": ["r1"]},
    ]
    return agents, plan_runs


def _through_plan_review_resolved() -> list[SSEEvent]:
    """挂起前 content → plan_review_required → resolved（不含续跑正文）。"""
    agents, plan_runs = _plan_review_agents_and_runs()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta(_PRE_PAUSE),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="分阶段",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_completed(
            "r1",
            "w1",
            output_summary="方案就绪",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        plan_review_required(
            checkpoint_id="cp1",
            conversation_id=_CONV,
            steps=[{"run_id": "r1", "role": "调研", "summary": "方案就绪"}],
            pending=[{"run_id": "r2", "role": "执行"}],
        ),
        plan_review_resolved(checkpoint_id="cp1", decision="continue"),
    ]


def _resume_content_continuity() -> list[SSEEvent]:
    """挂起前 content → plan_review → resolved → 续跑 content → end；正文续拼。"""
    return [
        *_through_plan_review_resolved(),
        content_delta(_POST_RESUME),
        message_end(FinishReason.END_TURN, input_tokens=3000, output_tokens=400, cost=_COST),
    ]


def _resume_content_reset_reinject() -> list[SSEEvent]:
    """resolved 后 content → content_reset → 重灌 delta(pre_pause) → 重写 → end（G6）。"""
    return [
        *_through_plan_review_resolved(),
        content_delta(_DRAFT_DISCARDED),
        content_reset("finish_guard"),
        # G6 display-only reinject encoded as ordinary content_delta (oracle 零改动).
        content_delta(_PRE_PAUSE + "\n\n"),
        content_delta(_REWRITE),
        message_end(FinishReason.END_TURN, input_tokens=3200, output_tokens=420, cost=_COST),
    ]


def _resume_ask_user_absorb() -> list[SSEEvent]:
    """ask_user 吸收 → resolved → 续跑；content_reset 清气泡、卡片承载问句。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta(_ABSORB_PROSE),
        content_reset("ask_user"),
        checkpoint_required(
            checkpoint_id="cp_absorb",
            conversation_id=_CONV,
            question=_ABSORB_PROSE + "\n请确认后继续。",
            intent="kickoff",
        ),
        checkpoint_resolved(checkpoint_id="cp_absorb", decision="continue", note="继续"),
        content_delta(_ABSORB_CONTINUE),
        message_end(FinishReason.END_TURN, input_tokens=2100, output_tokens=260, cost=_COST),
    ]


VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    "resume_content_continuity": (
        "挂起恢复：plan_review 前后正文续拼（resumed reload == live）",
        _resume_content_continuity,
    ),
    "resume_content_reset_reinject": (
        "挂起恢复：content_reset 后重灌 pre_pause delta 再重写（G6）",
        _resume_content_reset_reinject,
    ),
    "resume_ask_user_absorb": (
        "挂起恢复：ask_user 吸收（content_reset）后气泡空基底、卡片承载问句",
        _resume_ask_user_absorb,
    ),
}
