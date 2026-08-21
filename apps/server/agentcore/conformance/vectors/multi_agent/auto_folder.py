"""裸聊写盘自动建文件夹向量（双模式工作区 §5.4 裸聊行）."""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    auto_folder_created,
    content_delta,
    message_end,
    message_start,
    run_completed,
    run_plan,
    run_started,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE


def _multi_agent_auto_folder_created() -> list[SSEEvent]:
    """裸聊没选文件夹就要写盘：运行时按话题建好云文件夹。

    发射点与生产一致——``delegate`` 的 ``tool_use_start`` 之后、``run_plan`` 之前
    （``ensure_bare_chat_auto_cloud_desk`` 在 delegate 开头跑）。不是审批：本回合
    照常派工收口，事件流里没有任何挂起帧；对话内也不再画落点条。
    """
    agents = [{"id": "w1", "role": "资料整理", "thinking": True}]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "整理竞品定价要点成文档", "depends_on": []}
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来整理成一份文档。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "资料整理"}]}),
        auto_folder_created(
            folder_id="fld_auto_1",
            name="竞品定价调研",
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="整理竞品定价要点",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_completed(
            "r1",
            "w1",
            output_summary="要点文档已落盘",
            duration_ms=1400,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            output_files=["竞品定价要点.md"],
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已完成。"),
        content_delta("要点已整理完成，文件在「竞品定价调研」文件夹里。"),
        message_end(FinishReason.END_TURN, input_tokens=1500, output_tokens=260, cost=_COST),
    ]
