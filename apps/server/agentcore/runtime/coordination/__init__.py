"""CEO 协调模式（Phase 2–3）：非阻塞 delegate + 事件驱动协调循环。

→ 见 docs/03-AI核心/编排器与CEO主Agent.md §协调模式、
docs/03-AI核心/执行引擎架构设计.md §协调事件注入
"""

from __future__ import annotations

from agentcore.runtime.coordination.session import (
    DEFAULT_COORDINATION_BUDGET,
    MAX_COORDINATION_BUDGET,
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    CoordinationSnapshot,
    active_coordination,
    active_coordination_for_conversation,
    adopt_active_execution,
    await_live_detached_drive,
    bind_host_journal,
    cancel_coordination_on_user_stop,
    clear_active_coordination,
    coordination_budget_for_batch,
    current_execution_id,
    finish_detached_coordination,
    release_turn_coordination,
    resolve_coordination_session,
    set_active_coordination,
    should_enter_coordination,
    split_coordination_budget,
)

__all__ = [
    "DEFAULT_COORDINATION_BUDGET",
    "MAX_COORDINATION_BUDGET",
    "CoordinationEvent",
    "CoordinationEventKind",
    "CoordinationSession",
    "CoordinationSnapshot",
    "active_coordination",
    "active_coordination_for_conversation",
    "adopt_active_execution",
    "await_live_detached_drive",
    "bind_host_journal",
    "cancel_coordination_on_user_stop",
    "clear_active_coordination",
    "coordination_budget_for_batch",
    "current_execution_id",
    "finish_detached_coordination",
    "release_turn_coordination",
    "resolve_coordination_session",
    "set_active_coordination",
    "should_enter_coordination",
    "split_coordination_budget",
]
