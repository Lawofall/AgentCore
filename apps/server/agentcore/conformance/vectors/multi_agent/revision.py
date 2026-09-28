"""Multi-agent revision / batch / nested lead-subplan vectors."""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    escalation_raised,
    message_end,
    message_start,
    plan_revised,
    run_completed,
    run_context,
    run_output_delta,
    run_plan,
    run_progress,
    run_started,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE, _ctx_block


def _multi_agent_revision() -> list[SSEEvent]:
    """多 Agent：同人续派（原 revise 迁续派语义）。续写 run 携 ``continues_run_id``（星型根），
    不在 plan 里——三端都从其 ``run_started`` 帧合成出续派节点 + 继承原 agent 身份的新 agent。"""
    agents = [
        {
            "id": "w1",
            "role": "撰写员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "起草", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="起草并修订",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_output_delta("r1", "w1", "初稿"),
        run_completed(
            "r1",
            "w1",
            output_summary="初稿完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r1v2", "w1b", parent_run_id=None, continues_run_id="r1"),
        # 定向唤回热修：修订节点也 emit run_context——承载 CEO 喂给它的「修订要求」(revision 通道)，
        # 于是非辩论 V2 节点的「收到的上下文」也有内容，而非空白（用户看到的 == LLM 吃到的）。
        run_context(
            "r1v2",
            "w1b",
            [_ctx_block("continuation", "本次修订要求（定向唤回）", "补一段风险对冲，并收紧结论口径。")],
        ),
        run_output_delta("r1v2", "w1b", "修订稿"),
        run_completed(
            "r1v2",
            "w1b",
            output_summary="修订完成",
            duration_ms=600,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_redelegate_continuation() -> list[SSEEvent]:
    """多 Agent：delegate 同批 ``depends_on`` + ``continue_from_run_id``。

    r1 冷开局完成 → r2 在计划内带 continue_from=r1 续写（wire 携 continues_run_id=r1，
    parent=captain）。与 multi_agent_revision（计划外合成）互补：本向量钉「计划内续派节点」。
    """
    agents = [
        {
            "id": "w1",
            "role": "研究员",
            "thinking": True,
        },
        {
            "id": "w1b",
            "role": "研究员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "先调研", "depends_on": []},
        {
            "id": "r2",
            "agent_id": "w1b",
            "task": "据调研接着写实现要点",
            "depends_on": ["r1"],
        },
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研后同人续写",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("cap", "cap", kind="captain"),
        run_started("r1", "w1", parent_run_id="cap"),
        run_output_delta("r1", "w1", "调研稿"),
        run_completed(
            "r1",
            "w1",
            output_summary="调研完成",
            duration_ms=700,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(
            "r2",
            "w1b",
            parent_run_id="cap",
            continues_run_id="r1",
        ),
        run_context(
            "r2",
            "w1b",
            [
                _ctx_block("continuation", "续干指令", "据调研接着写实现要点"),
                _ctx_block(
                    "dependency",
                    "上游产物（r1）",
                    "调研稿",
                    source_run_id="r1",
                    fidelity="pass_through",
                ),
            ],
        ),
        run_output_delta("r2", "w1b", "实现要点……"),
        run_completed(
            "r2",
            "w1b",
            output_summary="续写完成",
            duration_ms=500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "cap",
            "cap",
            output_summary="已安排同人续派",
            duration_ms=100,
            role="captain",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        message_end(FinishReason.END_TURN, input_tokens=2200, output_tokens=450, cost=_COST),
    ]


def _multi_agent_plan_revised() -> list[SSEEvent]:
    """多 Agent：历史 leftover「计划已调整」轻痕迹（live replan 不再发 kind=bind，仅 fold）。
    旧向量含待定稿下游（r2 撰写）+ 未跑下游（r3 复核）。r1 调研跑完触到波边界 → CEO
    据上游产出调 ``replan``：定稿 r2 的职责（``bind``）并操舵 r3 的复核重点（``steer``），发一条
    ``plan_revised`` → 三端把 ``revised`` 折到对应节点（r2=bind / r3=steer，r1 恒 None）；定稿/操舵
    后同一 DAG 续跑 r2、r3。验「自我纠偏看得见不打断」：节点带轻痕迹，回合照常 end_turn。
    ``revised`` 随 ``run_plan`` 节点默认 None，故所有既有向量的 runs 也都新增 ``revised: null``。"""
    agents = [
        {
            "id": "w1",
            "role": "研究员",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "撰写员",
            "thinking": True,
        },
        {
            "id": "w3",
            "role": "复核员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研竞品定价", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "（依赖完成后再定稿）", "depends_on": ["r1"]},
        {"id": "r3", "agent_id": "w3", "task": "复核成稿", "depends_on": ["r2"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="定价分析（含晚绑定下游）",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_output_delta("r1", "w1", "竞品定价区间……"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成竞品定价调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        # 波边界让出 → CEO 据 r1 产出定稿 r2（bind）并操舵 r3 复核重点（steer），发轻痕迹。
        plan_revised(
            execution_id="exec1",
            revisions=[
                {"run_id": "r2", "kind": "bind"},
                {"run_id": "r3", "kind": "steer"},
            ],
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "据调研撰写定价建议……"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成定价建议",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r3", "w3"),
        run_output_delta("r3", "w3", "复核通过，补一处口径……"),
        run_completed(
            "r3",
            "w3",
            output_summary="复核完成",
            duration_ms=700,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 团队已完成（计划据中途发现调整过）。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]

def _multi_agent_multi_batch() -> list[SSEEvent]:
    """多 Agent：同一回合两批 ``delegate``（同 ``execution_id``）。第二批合并进现有图（不重置），
    进度跨批累计（来自 run 状态、非每批 run_progress 计数器）。"""
    batch1_agents = [
        {
            "id": "w1",
            "role": "研究员",
            "thinking": True,
        },
    ]
    batch1_runs = [{"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []}]
    batch2_agents = [
        {
            "id": "w2",
            "role": "撰写员",
            "thinking": True,
        },
    ]
    batch2_runs = [{"id": "r2", "agent_id": "w2", "task": "撰写", "depends_on": ["r1"]}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("先调研。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="分两批",
            agents=batch1_agents,
            runs=batch1_runs,
        ),
        run_started("r1", "w1"),
        run_output_delta("r1", "w1", "调研完成"),
        run_completed(
            "r1",
            "w1",
            output_summary="调研结论",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 再撰写。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="分两批",
            agents=batch2_agents,
            runs=batch2_runs,
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "成稿"),
        run_completed(
            "r2",
            "w2",
            output_summary="撰写完成",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        message_end(FinishReason.END_TURN, input_tokens=5000, output_tokens=900, cost=_COST),
    ]


def _multi_agent_multi_batch_disjoint() -> list[SSEEvent]:
    """多 Agent：同回合两批 ``delegate``（同 ``execution_id``），跨批**无** ``depends_on``。

    第一批是小链（调研→分析）；第二批在第一批部分完成后到达，另起一条独立链（撰写→审校）。
    用来钉前端协作图在「两坨互不相连」时的呈现——协议层不携带批次元数据，图上不应伪造依赖边。
    """
    batch1_agents = [
        {
            "id": "w1",
            "role": "研究员",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "分析师",
            "thinking": True,
        },
    ]
    batch1_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研素材", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "分析结论", "depends_on": ["r1"]},
    ]
    batch2_agents = [
        {
            "id": "w3",
            "role": "撰写员",
            "thinking": True,
        },
        {
            "id": "w4",
            "role": "审校员",
            "thinking": True,
        },
    ]
    batch2_runs = [
        {"id": "r3", "agent_id": "w3", "task": "撰写文稿", "depends_on": []},
        {"id": "r4", "agent_id": "w4", "task": "审校定稿", "depends_on": ["r3"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("先调研分析。"),
        tool_use_start(
            "dc1",
            "delegate",
            {
                "tasks": [{"role": "研究员"}, {"role": "分析师"}],
                "coordinate": True,
            },
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="两批独立任务线",
            agents=batch1_agents,
            runs=batch1_runs,
        ),
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output="【团队已启动·协调模式】已派出 2 名队员（研究员、分析师）。",
        ),
        run_started("r1", "w1"),
        run_output_delta("r1", "w1", "素材就绪"),
        run_completed(
            "r1",
            "w1",
            output_summary="调研完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_progress(1, 2),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "分析进行中…"),
        # 第二批在第一批仍有人在跑时到达；跨批无 depends_on。
        content_delta(" 并行追加撰写审校。"),
        tool_use_start(
            "dc2",
            "delegate",
            {
                "tasks": [{"role": "撰写员"}, {"role": "审校员"}],
                "coordinate": True,
            },
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="两批独立任务线",
            agents=batch2_agents,
            runs=batch2_runs,
        ),
        tool_use_end(
            "dc2",
            "delegate",
            success=True,
            output="【团队已启动·协调模式】已派出 2 名队员（撰写员、审校员）。",
        ),
        run_started("r3", "w3"),
        run_output_delta("r2", "w2", "分析定稿"),
        run_completed(
            "r2",
            "w2",
            output_summary="分析完成",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_progress(2, 4),
        run_output_delta("r3", "w3", "文稿草稿"),
        run_completed(
            "r3",
            "w3",
            output_summary="撰写完成",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_progress(3, 4),
        run_started("r4", "w4"),
        run_output_delta("r4", "w4", "审校通过"),
        run_completed(
            "r4",
            "w4",
            output_summary="审校完成",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_progress(4, 4),
        content_delta(" 两批任务线均已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=6200, output_tokens=1100, cost=_COST),
    ]

def _multi_agent_lead_subplan_bind_replan() -> list[SSEEvent]:
    """多 Agent·嵌套 lead 历史 leftover 晚定稿续跑（live 不再走 bind_after_deps；仅 fold）。
    CEO 把「整块后端」交给一个 lead（L1，顶层 worker）；L1 上手后自己扇出一支子队
    （sa 子调研 + sb 待定稿），子队 run_plan 与 L1 **同一 execution_id** → 三端按
    ``parent_run_id`` 合并进同一张团队图（不 reset，子节点挂在 L1 下）。sa 跑完触到 L1 子计划的波
    边界 → L1【自己】调 replan 定稿 sb（``plan_revised`` kind=bind，execution_id 仍是这同一 id）→ 三端
    把 ``revised=bind`` 折到子节点 sb（其余节点恒 None）；定稿后续跑 sb。这是 B 的去特例闭环在 UI 折叠
    层的契约：**lead（非根 CEO）的自主再绑定看得见、且嵌套图按 parent_run_id 不串层**，回合照常 end_turn。"""
    lead_agent = {
        "id": "L1",
        "role": "后端负责人",
        "thinking": True,
    }
    sub_agents = [
        {
            "id": "sa",
            "role": "子研究员",
            "thinking": True,
        },
        {
            "id": "sb",
            "role": "子撰写员",
            "thinking": True,
        },
    ]
    # lead 语义由嵌套扇出（同 execution_id + parent_run_id）表达；委派默认开、
    # 无 opt-in 开关，SSE wire 本就不带该类字段（见 plan_events.run_payload）。
    lead_runs = [
        {
            "id": "L1",
            "agent_id": "L1",
            "task": "负责整个后端",
            "depends_on": [],
        }
    ]
    sub_runs = [
        {
            "id": "sa",
            "agent_id": "sa",
            "task": "子调研接口现状",
            "depends_on": [],
            "parent_run_id": "L1",
        },
        {
            "id": "sb",
            "agent_id": "sb",
            "task": "（依赖完成后再定稿）",
            "depends_on": ["sa"],
            "parent_run_id": "L1",
        },
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来把后端整块交给一位负责人。"),
        # ① CEO 的团队：一个 lead 顶层 worker。team 标记落在首个 run_plan。
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="构建后端（下放给负责人自行拆解）",
            agents=[lead_agent],
            runs=lead_runs,
        ),
        run_started("L1", "L1"),
        # ② L1 上手后自己扇出子队（同 execution_id → 合并进图，子节点挂在 L1 下，不再发 team 标记）。
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="后端子团队（含晚绑定下游）",
            agents=sub_agents,
            runs=sub_runs,
        ),
        run_started("sa", "sa", parent_run_id="L1"),
        run_output_delta("sa", "sa", "现有接口与缺口……"),
        run_completed(
            "sa",
            "sa",
            output_summary="完成子调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        # ③ 子计划波边界让出 → L1【自己】据 sa 产出定稿 sb（bind）。execution_id 仍是同一子图所属 id。
        plan_revised(
            execution_id="exec1",
            revisions=[{"run_id": "sb", "kind": "bind"}],
        ),
        run_started("sb", "sb", parent_run_id="L1"),
        run_output_delta("sb", "sb", "据子调研撰写接口方案……"),
        run_completed(
            "sb",
            "sb",
            output_summary="完成子撰写",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        # ④ L1 整合子队产出，自身节点完成。
        run_completed(
            "L1",
            "L1",
            output_summary="后端整块完成",
            duration_ms=3000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 后端已完成（负责人中途自行定稿了一个晚绑定步骤）。"),
        message_end(FinishReason.END_TURN, input_tokens=5200, output_tokens=960, cost=_COST),
    ]


def _multi_agent_lead_subplan_scope_steer() -> list[SSEEvent]:
    """多 Agent·嵌套 lead 据子队员偏离操舵子计划 (受监督子计划 B 的 SCOPE 臂, 自底向上)。CEO 把整块
    交给 lead（L1）；L1 扇出子队（sa 子调研 + sb 子撰写，sb 依赖 sa）。sa 执行中发现真正要做的与初始
    子计划不符，调 ``escalate reason=scope`` 报偏离 → 三端把该升级折到子节点 sa（协作图标记，
    工人继续干，回合不 paused）。其未跑下游 sb 触发 L1 子计划的 SCOPE 波边界 → L1【自己】调 replan 操舵 sb
    （``plan_revised`` kind=steer）→ revised=steer 折到 sb；据偏离续跑 sb。验「lead 自底向上据证据
    重规划在 UI 折叠层成立、嵌套图不串层」。"""
    lead_agent = {
        "id": "L1",
        "role": "前端负责人",
        "thinking": True,
    }
    sub_agents = [
        {
            "id": "sa",
            "role": "子研究员",
            "thinking": True,
        },
        {
            "id": "sb",
            "role": "子撰写员",
            "thinking": True,
        },
    ]
    # lead 语义由嵌套扇出（同 execution_id + parent_run_id）表达；委派默认开、
    # 无 opt-in 开关，SSE wire 本就不带该类字段（见 plan_events.run_payload）。
    lead_runs = [
        {
            "id": "L1",
            "agent_id": "L1",
            "task": "负责整个前端",
            "depends_on": [],
        }
    ]
    sub_runs = [
        {
            "id": "sa",
            "agent_id": "sa",
            "task": "子调研真实交互需求",
            "depends_on": [],
            "parent_run_id": "L1",
        },
        {
            "id": "sb",
            "agent_id": "sb",
            "task": "撰写子页面方案",
            "depends_on": ["sa"],
            "parent_run_id": "L1",
        },
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来把前端整块交给一位负责人。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="构建前端（下放给负责人自行拆解）",
            agents=[lead_agent],
            runs=lead_runs,
        ),
        run_started("L1", "L1"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="前端子团队",
            agents=sub_agents,
            runs=sub_runs,
        ),
        run_started("sa", "sa", parent_run_id="L1"),
        # 子队员 sa 报告职责偏离（escalate reason=scope）→ 折到 sa 节点（协作图标记）。
        escalation_raised(
            "sa",
            "sa",
            question="真正要做的是 X 而非初始子计划的 Y，下游写法应随之调整。",
            assumption="暂按 X 推进",
            kind="adjust",
            # 固定 id 保 golden 稳定（缺省会随机 uuid，导出不幂等）。
            escalation_id="esc-scope1",
        ),
        run_output_delta("sa", "sa", "已按 X 完成子调研"),
        run_completed(
            "sa",
            "sa",
            output_summary="完成子调研（含 1 条偏离）",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        # 未跑下游 sb 触 SCOPE 波边界 → L1【自己】据偏离操舵 sb（steer）。
        plan_revised(
            execution_id="exec1",
            revisions=[{"run_id": "sb", "kind": "steer"}],
        ),
        run_started("sb", "sb", parent_run_id="L1"),
        run_output_delta("sb", "sb", "据校准后的 X 撰写页面方案……"),
        run_completed(
            "sb",
            "sb",
            output_summary="完成子撰写（已据偏离校准）",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "L1",
            "L1",
            output_summary="前端整块完成",
            duration_ms=3000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 前端已完成（负责人据子队员的偏离信号中途校准了下游）。"),
        message_end(FinishReason.END_TURN, input_tokens=5200, output_tokens=960, cost=_COST),
    ]


def _multi_agent_lead_peer_mixed_overlap() -> list[SSEEvent]:
    """多 Agent·嵌套 lead + 平级同名角色混合（反模式回归）。CEO 同批既派一个再带子队的
    lead（L1，委派默认开），又平级直派与 lead 同名角色（前端工程师）及另一平级角色。
    引擎【不拒单】——本向量钉 wire/fold：同批扁平挂 CEO 下、lead 仍可再扇出子队（sub 挂 L1），
    进度与图不串层。此形状由 prompt 拆活纪律（同一摊二选一、勿双开）避免，运行时不去重、不拦截。"""
    agents = [
        {
            "id": "L1",
            "role": "前端工程师",
            "thinking": True,
        },
        {
            "id": "w_fe",
            "role": "前端工程师",
            "thinking": True,
        },
        {
            "id": "w_be",
            "role": "后端工程师",
            "thinking": True,
        },
        {
            "id": "sa",
            "role": "子实现",
            "thinking": True,
        },
    ]
    # L1 为 lead 由后续子队 parent_run_id="L1" 表达（wire 无委派开关字段）。
    top_runs = [
        {
            "id": "L1",
            "agent_id": "L1",
            "task": "带队拆前端（嵌套）",
            "depends_on": [],
        },
        {
            "id": "w_fe",
            "agent_id": "w_fe",
            "task": "平级直派前端（与 lead 同名）",
            "depends_on": [],
        },
        {
            "id": "w_be",
            "agent_id": "w_be",
            "task": "平级直派后端",
            "depends_on": [],
        },
    ]
    sub_runs = [
        {
            "id": "sa",
            "agent_id": "sa",
            "task": "子实现组件",
            "depends_on": [],
            "parent_run_id": "L1",
        },
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我会组一支团队（含嵌套 lead 与平级同名角色）。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="嵌套 lead + 平级混合（反模式）",
            agents=agents[:3],
            runs=top_runs,
        ),
        run_started("L1", "L1"),
        run_started("w_fe", "w_fe"),
        run_started("w_be", "w_be"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="L1 子团队",
            agents=[agents[3]],
            runs=sub_runs,
        ),
        run_started("sa", "sa", parent_run_id="L1"),
        run_output_delta("sa", "sa", "子实现完成……"),
        run_completed(
            "sa",
            "sa",
            output_summary="完成子实现",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "L1",
            "L1",
            output_summary="lead 整合完成",
            duration_ms=2000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "w_fe",
            "w_fe",
            output_summary="平级前端完成",
            duration_ms=1500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "w_be",
            "w_be",
            output_summary="平级后端完成",
            duration_ms=1500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 混合批次已跑完（引擎未拒单）。"),
        message_end(FinishReason.END_TURN, input_tokens=4800, output_tokens=900, cost=_COST),
    ]
