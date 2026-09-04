"""辩论编排 SSE 事件与主持人计费。"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from agentcore.runtime.debate import DebateConfig
from agentcore.runtime.debate.constants import FORM_LABELS
from agentcore.runtime.events import run_completed, run_failed, run_plan

if TYPE_CHECKING:
    from agentcore.runtime.debate.moderator import Moderator
    from agentcore.tools.builtin.debate.tool import DebateTool


def debate_act_payload(tool: DebateTool) -> dict[str, Any]:
    """幕声明：独立辩论 = act-1；链上一张 MLR = 下一幕（anchor=汇总员 + prev）。"""
    act_id = getattr(tool, "_debate_act_id", None) or "act-1"
    act: dict[str, Any] = {"act_id": act_id, "kind": "debate"}
    title = getattr(tool, "_debate_act_title", None)
    if title:
        act["title"] = title
    anchor = getattr(tool, "_debate_anchor_run_id", None)
    if anchor:
        act["anchor_run_id"] = anchor
    authorized_by = getattr(tool, "_debate_authorized_by", None)
    if authorized_by in ("stage_card", "auto", "preview"):
        act["authorized_by"] = authorized_by
    return act


def moderator_plan_event(
    tool: DebateTool, execution_id: str, moderator_run_id: str, config: DebateConfig
):
    """声明主持人节点（CEO 之下、辩手之上的编排角色）。

    独立辩论 / 跨回合新图+prev / 同回合加一幕：主持人 ``parent_run_id`` 引用本回合
    CEO captain。幕间因果经 ``act.anchor_run_id``；跨回合另带 ``prev_execution_id``
    （同回合复用宿主 eid，不写 prev，不再 divert 宿主图）。
    """
    label = FORM_LABELS.get(config.form, "辩论")
    parent = getattr(tool, "_debate_graph_parent_run_id", None) or tool._captain_run_id
    prev_execution_id = getattr(tool, "_debate_prev_execution_id", None)
    agents: list[dict[str, Any]] = [
        {
            "id": moderator_run_id,
            "role": "主持人",
            # 等辩手时不准出假「思考中」：run_plan 仍 thinking=False。判定 complete 带回的
            # 思考走既有 run_reasoning_delta（整段，非打字机），不改本声明。
            "thinking": False,
        }
    ]
    runs: list[dict[str, Any]] = [
        {
            "id": moderator_run_id,
            "agent_id": moderator_run_id,
            "task": f"主持{label}：{config.motion[:60]}",
            "depends_on": [],
            "parent_run_id": parent,
        }
    ]
    return run_plan(
        execution_id=execution_id,
        plan_type="debate",
        task_summary=f"{label}：{config.motion[:60]}",
        agents=agents,
        runs=runs,
        prev_execution_id=prev_execution_id,
        act=debate_act_payload(tool),
    )


def side_card(tool: DebateTool, node) -> dict[str, Any]:
    return {
        "id": node.agent_id,
        "role": node.role,
        "thinking": node.thinking,
    }


def run_payload(node) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": node.run_id,
        "agent_id": node.agent_id,
        "task": node.task,
        "depends_on": node.depends_on,
        "parent_run_id": node.parent_run_id,
    }
    if node.stance:
        payload["stance"] = node.stance
    if node.group:
        payload["group"] = node.group
    if node.round:
        payload["round"] = node.round
    return payload


def settle_moderator_node(
    tool: DebateTool,
    moderator: Moderator | None,
    moderator_run_id: str,
    model: str,
    *,
    summary: str,
    duration_ms: int,
    error: str = "",
) -> None:
    """主持人节点的**唯一终帧出口** —— 一帧必发、只发一次，用量两路都入账。

    ``error`` 为空 → ``run_completed``（正常收场 / plan-only 记录计划即止）；非空 →
    ``run_failed``（辩论中途崩溃）。此前只有成功分支发终帧，异常一路 ``return err(...)``
    直接走掉，协作图上的主持人节点永久停在 running、主持人自身那几次 LLM 调用（议题 /
    裁判 / 小结 / 简报）也整笔丢账——异常不是不花钱。

    幂等：成功路径在 ``debate_result`` 之前提前调一次以钉住线序（``run_completed`` →
    ``debate_result``，见 conformance 向量 ``debate/debate_single``），``_run_moderator``
    的 ``finally`` 再兜一次；先到先发，后到即返回。
    """
    from agentcore.llm.pricing import calculate_cost
    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.costing import ROLE_ARENA
    from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState

    if tool._moderator_settled:
        return
    tool._moderator_settled = True

    usage = moderator.usage if moderator is not None else TokenUsage()
    cost = calculate_cost(model, usage)
    if error:
        # run_failed 无 usage / cost 字段（全平台失败帧同形）：钱走 ``_acc`` 折回回合总账。
        tool._sink.emit(run_failed(moderator_run_id, moderator_run_id, error))
    else:
        tool._sink.emit(
            run_completed(
                moderator_run_id,
                moderator_run_id,
                output_summary=summary,
                duration_ms=duration_ms,
                role="主持人",
                model=model,
                usage=usage.as_dict(),
                cost=asdict(cost),
            )
        )
    if usage.total_tokens <= 0:
        return  # 无 LLM 用量（极端）则不另记账目，但主持人节点已落终态。
    spec = RunSpec(
        run_id=moderator_run_id,
        agent_id=moderator_run_id,
        task="主持辩论",
        role="主持人",
    )
    state = RunState(
        phase=RunPhase.FAILED if error else RunPhase.COMPLETED,
        model=model,
        usage=usage.as_dict(),
        cost=asdict(cost),
        rounds=moderator.llm_rounds if moderator is not None else 0,
    )
    tool._acc.add_run_cost(
        spec, state, parent_run_id=tool._captain_run_id, role=ROLE_ARENA
    )
    tool._acc.add_usage(usage.as_dict())
