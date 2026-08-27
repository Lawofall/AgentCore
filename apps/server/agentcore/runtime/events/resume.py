"""Cold-resume transport acks (``POST .../resume`` 的两种「不是新续跑」开场帧).

两帧都是 EPHEMERAL：执行事实的权威在 ``turn_journal``（唯一事实源）、结算结论的权威在
``paused_turn_outcomes``（消费掉暂停帧的那一方同事务写下），这里只告诉这条连接「你这次
提交落在了哪儿」——等槽（``resume_deferred``）还是别人已经决定过（``resume_settled``）。
同一张冷卡被点两次时，客户端靠它们把卡从待决收成结果态，而不是收到一个「挂起的回合不
存在」的 404。
"""

from __future__ import annotations

from typing import Literal

from agentcore.runtime.events.types import EventType, SSEEvent


def resume_deferred(
    *,
    message_id: str,
    conversation_id: str,
    busy_reason: Literal["wrap_up", "live_turn"],
) -> SSEEvent:
    """冷 resume × live deferred ack——settlement 已预写；槽空后同连接 claim + 续跑。"""
    return SSEEvent(
        type=EventType.RESUME_DEFERRED,
        payload={
            "message_id": message_id,
            "conversation_id": conversation_id,
            "busy_reason": busy_reason,
        },
    )


def resume_settled(
    *,
    message_id: str,
    conversation_id: str,
    kind: Literal["ask_user", "plan_review"],
    checkpoint_id: str,
    decision: str,
    decided_at: str,
    turn_status: Literal["running", "complete", "incomplete", "failed", "unknown"],
) -> SSEEvent:
    """冷 resume 幂等成功 ack——帧已被消费，带的是消费方写下的那份结论（不再 404）。"""
    return SSEEvent(
        type=EventType.RESUME_SETTLED,
        payload={
            "message_id": message_id,
            "conversation_id": conversation_id,
            "kind": kind,
            "checkpoint_id": checkpoint_id,
            "decision": decision,
            "decided_at": decided_at,
            "turn_status": turn_status,
        },
    )
