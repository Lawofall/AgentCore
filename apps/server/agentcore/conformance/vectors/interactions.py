"""Conformance vectors — interaction lifecycle (提问确认统一重构 P3).

Covers ratchet scenarios: resolved-reload / orphaned / approval sibling sweep.
Also lifts P1 DURABLE_VECTOR_WAIVERS.

Debate ambient steer is fire-and-forget (no blocking interaction / no
``debate_round_decision_*`` events) — covered by moderator unit tests +
``multi_agent_debate_followup`` (user_interjections on debate_result).
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    approval_required,
    approval_resolved,
    checkpoint_required,
    checkpoint_resolved,
    content_delta,
    interaction_orphaned,
    message_end,
    message_start,
)

from ._common import _CONV, _COST


def _checkpoint_resolved_reload() -> list[SSEEvent]:
    """resolved 后重载：required+resolved 都在 journal，fold 出已答态（不变量 4 / 实证故障 1）。

    对照单槽时代「resolved 写失败 → 重载回退成待答」：本向量断言 interactions[] 含
    status=resolved 的 ask_user，且无假 pending。
    """
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("开始前我确认一下方向："),
        checkpoint_required(
            checkpoint_id="cp1",
            conversation_id=_CONV,
            question="先做 A 还是 B？\n两条路线各有取舍。",
            intent="kickoff",
        ),
        checkpoint_resolved(checkpoint_id="cp1", decision="continue", note="选 A"),
        content_delta("好，按 A 推进。"),
        message_end(FinishReason.END_TURN, input_tokens=2100, output_tokens=260, cost=_COST),
    ]


def _approval_orphaned() -> list[SSEEvent]:
    """orphaned：required + interaction_orphaned → fold 出已失效态（重启假卡）。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我需要运行代码。"),
        approval_required(
            approval_id="tc1",
            conversation_id=_CONV,
            tool_call_id="tc1",
            tool_name="code_execute",
            arguments={"code": "print(1)"},
        ),
        interaction_orphaned(interaction_id="tc1", kind="approval"),
    ]


def _approval_sibling_sweep() -> list[SSEEvent]:
    """审批一键放行 sibling 清扫：多个 approval_required + 批量 resolved，fold 无假 pending。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("需要写几个文件。"),
        approval_required(
            approval_id="a1",
            conversation_id=_CONV,
            tool_call_id="a1",
            tool_name="file_write",
            arguments={"path": "a.txt", "content": "1"},
        ),
        approval_required(
            approval_id="a2",
            conversation_id=_CONV,
            tool_call_id="a2",
            tool_name="file_write",
            arguments={"path": "b.txt", "content": "2"},
        ),
        approval_required(
            approval_id="a3",
            conversation_id=_CONV,
            tool_call_id="a3",
            tool_name="file_write",
            arguments={"path": "c.txt", "content": "3"},
        ),
        # 一键放行：本卡 approve_always + sibling 批量 resolved
        approval_resolved(approval_id="a1", tool_call_id="a1", decision="approve_always"),
        approval_resolved(approval_id="a2", tool_call_id="a2", decision="approve"),
        approval_resolved(approval_id="a3", tool_call_id="a3", decision="approve"),
        content_delta(" 三个文件都写好了。"),
        message_end(FinishReason.END_TURN, input_tokens=1500, output_tokens=120, cost=_COST),
    ]


VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    "checkpoint_resolved_reload": (
        "检查点：required+resolved 重载呈已答态，无假 pending（P3 / 不变量 4）",
        _checkpoint_resolved_reload,
    ),
    "approval_orphaned": (
        "审批：required+orphaned → interactions[] 已失效态（P3 / 重启假卡）",
        _approval_orphaned,
    ),
    "approval_sibling_sweep": (
        "审批：多卡并发 + 批量 resolved，fold 无假 pending（P3 / sibling 清扫）",
        _approval_sibling_sweep,
    ),
}
