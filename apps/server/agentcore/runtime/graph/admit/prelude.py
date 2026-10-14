"""委派前奏：`delegate` 入参的零 await 校验 + 规范化。

从 `GraphTool.execute` 抽出（纯搬运）。这一段只读 `arguments` 与回合环境
（token 硬顶 / 鉴权死 / 工具表 / 深度），不碰协作图、不碰 DB、不写 `self`：
要么产出一份「规范化后的委派请求」，要么产出一条硬拒。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.llm.turn_auth_dead import (
    is_turn_auth_dead,
    turn_auth_dead_reject_message,
)
from agentcore.runtime.graph.admit.empty_tasks import EMPTY_DELEGATE_MSG
from agentcore.runtime.graph.admit.graph_args import coerce_node_items
from agentcore.runtime.runs.constants import MAX_WORKER_SUBDELEGATIONS
from agentcore.runtime.turn.token_budget import (
    current_turn_tokens,
    is_turn_token_ceiling_hit,
    resolve_turn_token_ceiling,
    turn_token_ceiling_reject_message,
)
from agentcore.tools.protocol import ToolResult

if TYPE_CHECKING:
    from agentcore.tools.registry import ToolRegistry

logger = get_logger(__name__)


def _has_wave_boundary_features(tasks_raw: list[Any]) -> bool:
    """True when any task has DAG edges (wave-boundary machinery)."""
    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        if task.get("depends_on"):
            return True
    return False


def _has_deep_deliverable_signal(tasks_raw: list[Any]) -> bool:
    """True when any task declares a landing deliverable (non-empty artifacts
    or artifact_dir). Omitted / empty object does not expect landing.
    """
    from agentcore.runtime.runs.types import task_raw_expects_landing

    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        if task_raw_expects_landing(task):
            return True
    return False


def _should_auto_light_delegate(tasks_raw: list[Any]) -> bool:
    """True when a single dependency-free worker needs no multi-agent coordination.

    Skips auto-light when the task expects on-disk landing (pinned artifacts /
    artifact_dir). Omitted / empty deliverable auto-lights.
    ``complexity_hint=light`` no longer stamps short ``max_rounds``; browser tool
    surfaces are not excluded from auto-light for round-budget reasons.
    """
    if len(tasks_raw) != 1:
        return False
    task = tasks_raw[0]
    if not isinstance(task, dict):
        return False
    if _has_wave_boundary_features([task]):
        return False
    return not _has_deep_deliverable_signal([task])


@dataclass(frozen=True)
class DelegatePreludeReject:
    """前奏硬拒：`execute` 原样返回 ``result``。"""

    result: ToolResult


@dataclass(frozen=True)
class DelegateBatchRequest:
    """规范化后的委派请求：前奏全绿时 `execute` 后续要用的全部输入。"""

    tasks_raw: list[Any]
    valid_tools: set[str]
    # CEO 可传任意值；下游按需 isinstance 收窄（build_run_plan）。
    complexity_hint: Any


def resolve_delegate_prelude(
    arguments: dict[str, Any],
    *,
    tools: ToolRegistry,
    depth: int,
    sub_workers_spawned: int,
    credential_source: str,
) -> DelegateBatchRequest | DelegatePreludeReject:
    """校验并规范化一次 `delegate` 调用（零 await；硬拒后规范化）。"""
    # Turn 级硬顶：禁新派（在飞不 cancel）；与 per-worker ceiling 正交。
    if is_turn_token_ceiling_hit():
        msg = turn_token_ceiling_reject_message()
        logger.info(
            "delegate.turn_token_ceiling_rejected",
            spent=current_turn_tokens(),
            ceiling=resolve_turn_token_ceiling(),
        )
        return DelegatePreludeReject(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                contract_failure=True,
            )
        )

    # 甲+乙：本回合该付款方 API Key 已鉴权死后禁再 delegate 烧同源调用。
    if is_turn_auth_dead(credential_source):
        msg = turn_auth_dead_reject_message(credential_source)
        logger.info("delegate.turn_auth_dead_rejected")
        return DelegatePreludeReject(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                contract_failure=True,
            )
        )

    raw_nodes = arguments.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        logger.info(
            "delegate.empty_rejected",
            has_nodes=bool(arguments.get("nodes")),
        )
        return DelegatePreludeReject(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=EMPTY_DELEGATE_MSG,
                contract_failure=True,
            )
        )
    tasks_raw, shape_err = coerce_node_items(raw_nodes, where="nodes")
    if shape_err:
        logger.info("delegate.rejected", errors=[shape_err], reason="node_shape")
        return DelegatePreludeReject(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=shape_err,
                contract_failure=True,
            )
        )

    valid_tools = {s.name for s in tools.list_all()}
    complexity_hint = arguments.get("complexity_hint", "standard")
    if "complexity_hint" not in arguments and _should_auto_light_delegate(tasks_raw):
        complexity_hint = "light"
        # info 级：档位归责的关键决策事件，debug 级曾导致线上排查只能靠 demo_tape 反推。
        logger.info("delegate.complexity_hint_inferred", hint="light")
    elif complexity_hint == "light" and _has_wave_boundary_features(tasks_raw):
        # 显式 light 与 DAG/波边界并存时忽略 light（避免关掉 on_boundary）。
        # 已删字数字段 / 钉路径 artifacts 不挡 light（修码快修）。
        complexity_hint = "standard"
        logger.info(
            "delegate.complexity_hint_ignored",
            reason="wave_boundary_features",
        )

    if depth >= 1:
        new_nodes = len(tasks_raw)
        if sub_workers_spawned + new_nodes > MAX_WORKER_SUBDELEGATIONS:
            msg = (
                f"子团队扇出已达上限（已派出 {sub_workers_spawned} 个 sub-worker，"
                f"本次 {new_nodes} 个，上限 {MAX_WORKER_SUBDELEGATIONS}）"
                "——合并任务或自己做完这张卡。"
            )
            logger.info(
                "delegate.sub_fanout_rejected",
                spawned=sub_workers_spawned,
                requested=new_nodes,
                cap=MAX_WORKER_SUBDELEGATIONS,
            )
            return DelegatePreludeReject(
                ToolResult(tool_call_id="", success=False, output="", error=msg),
            )
    return DelegateBatchRequest(
        tasks_raw=tasks_raw,
        valid_tools=valid_tools,
        complexity_hint=complexity_hint,
    )
