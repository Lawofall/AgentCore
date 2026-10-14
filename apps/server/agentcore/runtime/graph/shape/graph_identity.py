"""协作图身份解析：这批节点挂到本回合的哪次执行上。

同回合再拆，合入本回合当前图。跨回合不链上一张，也不把新节点写回已经收口的那一轮：
新节点记在本回合，整场对话的图由读侧把各轮折到一起。

观测领养（cancel / 插话）走 ``current_execution_id``。派单落在本回合 mint 的
``context_execution_id`` 上。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.runtime.graph.session import session as coordination_session
from agentcore.runtime.graph.shape import graph_append
from agentcore.tools.protocol import ToolResult

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState

def _is_same_host_turn_append(active: Any, message_id: str | None) -> bool:
    """True only for same-turn secondary delegate (message_id ≡ host_turn_id).

    Cross-turn adopt keeps the previous live graph for observation, but this
    turn's message_id differs from ``host_turn_id``, so it does not merge.
    Empty / unbound ``host_turn_id`` is not same-turn.
    """
    host_tid = (getattr(active, "host_turn_id", None) or "").strip()
    cur_tid = (message_id or "").strip()
    return bool(host_tid) and host_tid == cur_tid


def _is_live_execution_merge(active: Any, append_to: str | None) -> bool:
    """True when ``append_to`` targets a still-active coordination session."""
    if active is None or not getattr(active, "active", False):
        return False
    eid = (append_to or "").strip()
    if not eid:
        return False
    return eid == (getattr(active, "execution_id", None) or "").strip()


def _is_same_turn_merge(
    active: Any,
    *,
    message_id: str | None,
    calls: int,
    context_execution_id: str | None = None,
    last_graph_execution_id: str | None = None,
) -> bool:
    """合入仅限同回合：宿主 message_id 对上，或本工具实例二次+ 命中本回合图。"""
    if active is None or not getattr(active, "active", False):
        return False
    if _is_same_host_turn_append(active, message_id):
        return True
    if calls < 1:
        return False
    eid = (getattr(active, "execution_id", None) or "").strip()
    if not eid:
        return False
    mint = (context_execution_id or "").strip()
    last = (last_graph_execution_id or "").strip()
    return eid in (mint, last)


@dataclass(frozen=True)
class GraphIdentity:
    """本批 task 的协作图归属（`execute` 直接摊到同名局部变量上）。"""

    append_to: str | None = None
    prev_execution_id: str | None = None
    append_seed: dict | None = None
    host_plan_for_append: RunPlan | None = None
    host_captain_run_id: str | None = None


async def resolve_graph_identity(
    *,
    context_execution_id: str | None,
    message_id: str | None,
    captain_run_id: str | None,
    calls: int,
    last_graph_execution_id: str | None,
    last_graph_plan: RunPlan | None,
    last_graph_seed: dict[str, RunState] | None,
) -> GraphIdentity | ToolResult:
    """解析协作图身份，或返回硬拒 ToolResult。

    ``calls`` / ``last_graph_*`` = 工具实例上的同回合上一张图快照（`_calls` /
    `_last_graph_execution_id` / `_last_graph_plan` / `_last_graph_seed`）。
    """
    append_to: str | None = None
    prev_execution_id: str | None = None
    append_seed: dict | None = None
    host_plan_for_append = None

    # 同回合再拆：活跃 live_plan，或本工具实例第二次及以后合入上一批。
    # 跨回合不链、不合入旧图。
    active = coordination_session.active_coordination(context_execution_id)
    if (
        active is not None
        and active.active
        and getattr(active, "live_plan", None) is not None
        and _is_same_turn_merge(
            active,
            message_id=message_id,
            calls=calls,
            context_execution_id=context_execution_id,
            last_graph_execution_id=last_graph_execution_id,
        )
    ):
        host_plan_for_append = active.live_plan
    elif calls >= 1:
        last_eid = last_graph_execution_id
        last_plan = last_graph_plan
        if isinstance(last_eid, str) and last_eid.strip():
            append_to = last_eid.strip()
            # 同回合内存宿主：无 journal 亦可合入（阻塞单人跑完常见）。
            if last_plan is not None:
                host_plan_for_append = last_plan
                last_seed = last_graph_seed
                if last_seed is not None:
                    append_seed = last_seed

    host_captain_run_id: str | None = None
    if append_to:
        memory_host = host_plan_for_append is not None
        active = coordination_session.active_coordination(
            append_to
        ) or coordination_session.active_coordination(context_execution_id)
        same_turn = _is_same_turn_merge(
            active,
            message_id=message_id,
            calls=calls,
            context_execution_id=context_execution_id,
            last_graph_execution_id=last_graph_execution_id,
        ) or (memory_host and calls >= 1)
        live_merge = same_turn and (
            _is_live_execution_merge(active, append_to) or memory_host
        )

        if live_merge and host_plan_for_append is None and active is not None:
            host_plan_for_append = getattr(active, "live_plan", None)
        if not live_merge or host_plan_for_append is None:
            msg = (
                f"既有协作图 `{append_to}` 缺少可合并的计划快照（plan_snapshot），"
                "无法合入。请新建团队执行。"
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                contract_failure=True,
            )
        host_captain_run_id = graph_append.parse_host_captain_run_id(
            await graph_append.load_host_journal_entries(
                (getattr(active, "host_turn_id", None) or "")
                if active is not None
                else ""
            )
        ) or captain_run_id

    return GraphIdentity(
        append_to=append_to,
        prev_execution_id=prev_execution_id,
        append_seed=append_seed,
        host_plan_for_append=host_plan_for_append,
        host_captain_run_id=host_captain_run_id,
    )
