"""Conformance vectors that exercise the turnOutcome sidecar (not fold-only).

These probe the optional ``turnVerdict`` sidecar: team-host flags
(``hasTeamStrip`` + ``supportPackHost``) and the unproductive-with-body
failed-tool hint.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    error_event,
    message_end,
    message_start,
    run_failed,
    run_plan,
    run_started,
    tool_use_end,
    tool_use_start,
)

from ._common import _CONV, _COST


def _turn_verdict_team_host() -> list[SSEEvent]:
    """Team graph + attested error: strip owns the verdict on both native encodings."""
    agents = [{"id": "w1", "role": "研究员", "thinking": True}]
    plan_runs = [{"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排调研。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "研究员"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研 X",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_failed("r1", "w1", "调研失败", failure_kind="quality"),
        tool_use_end("dc1", "delegate", success=True, output="团队完成（含 1 项失败）。"),
        error_event("LLM_ERROR", "本轮未能完成，请重试。"),
        content_delta(" 调研未完成。"),
        message_end(
            FinishReason.ERROR,
            input_tokens=2000,
            output_tokens=400,
            cost=_COST,
            outcome="error",
        ),
    ]


def _turn_verdict_unproductive_body_tool() -> list[SSEEvent]:
    """Body + unproductive + a failed tool — desktop hint lists the tool name."""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("已写完大半"),
        tool_use_start("tc1", "host", {"action": "shell", "command": "do_work"}),
        tool_use_end(
            "tc1",
            "host",
            success=False,
            output="host failed: CalledProcessError: exit 1",
            failure={"message": "命令执行失败", "code": "TOOL_ERROR"},
        ),
        message_end(
            FinishReason.UNPRODUCTIVE,
            input_tokens=800,
            output_tokens=40,
            cost=_COST,
        ),
    ]


VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    "turn_verdict_team_host": (
        "判决对账：团队图 + attested error → 条是主判决（hasTeamStrip + supportPackHost）",
        _turn_verdict_team_host,
    ),
    "turn_verdict_unproductive_body_tool": (
        "判决对账：有正文 unproductive + 失败工具 → 工具名提示",
        _turn_verdict_unproductive_body_tool,
    ),
}
