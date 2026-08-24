"""Conformance vector builders — interactive gate pause/continue scenarios.

See ``vectors/__init__.py`` for the aggregated ``VECTORS`` registry.
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
    debate_round_started,
    execution_completed,
    message_end,
    message_start,
    plan_review_required,
    plan_review_resolved,
    reasoning_delta,
    run_completed,
    run_output_delta,
    run_plan,
    run_skipped,
    run_started,
    tool_use_end,
    tool_use_start,
)

from ._common import _CONV, _COST, _USAGE
from .debate._builders import (
    _moderator_agents_runs,
    _pro_con_debater_agents,
    _pro_con_debater_runs,
)


def _approval_paused() -> list[SSEEvent]:
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
    ]

def _approval_resolved_continue() -> list[SSEEvent]:
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
        approval_resolved(approval_id="tc1", tool_call_id="tc1", decision="approve"),
        tool_use_start("tc1", "code_execute", {"code": "print(1)"}),
        tool_use_end("tc1", "code_execute", success=True, output="1\n"),
        content_delta("运行结果是 1。"),
        message_end(FinishReason.END_TURN, input_tokens=900, output_tokens=80, cost=_COST),
    ]

def _plan_review_paused() -> list[SSEEvent]:
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
    return [
        message_start("m1", conversation_id=_CONV),
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
    ]

def _plan_review_resolved_continue() -> list[SSEEvent]:
    base = _plan_review_paused()
    return [
        *base,
        plan_review_resolved(checkpoint_id="cp1", decision="continue"),
        run_started("r2", "w2"),
        run_completed(
            "r2",
            "w2",
            output_summary="已落地",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        message_end(FinishReason.END_TURN, input_tokens=3000, output_tokens=400, cost=_COST),
    ]

def _single_agent_checkpoint() -> list[SSEEvent]:
    """单聊·检查点 (ask_user blocking=true)：CEO 想清楚后向用户拍板、暂停回合。检查点在时间线
    **原位**落一个 `checkpoint` 标记（卡片正文另路 fold，按 checkpoint_id 取回），回合停在
    checkpoint_required（无 message_end）→ pendingInteraction=checkpoint、status=paused。
    验「检查点不再压到气泡底部、而是回到它真实发生的时序位」。"""
    return [
        message_start("m1", conversation_id=_CONV),
        reasoning_delta("这个需求有歧义，先问清楚。"),
        content_delta("开始前我确认一下方向："),
        checkpoint_required(
            checkpoint_id="cp1",
            conversation_id=_CONV,
            question="先做 A 还是 B？\n两条路线各有取舍。",
            intent="kickoff",
        ),
    ]

def _single_agent_checkpoint_finalized() -> list[SSEEvent]:
    """单聊·检查点【收口即终止】(②)：ask_user(blocking) 落帧后【不再
    挂在内存 Future】，回合直接以 ``message_end(finish_reason=paused)`` 收口——流到此【终止】（对照
    ``_single_agent_checkpoint`` 的「停在 ``checkpoint_required``、无 ``message_end``」挂起态）。
    关键断言：``status`` 仍 ``paused``、``pendingInteraction`` 仍 checkpoint（同一张 resume 卡），但
    ``finishReason="paused"`` + ``cost`` 落账——客户端据「流以 paused 收尾」渲成单张 resume 卡（统一
    冷路 ``POST .../resume``，根除 live/durable 双态）。验「终止式挂起 == 挂起态的同一恢复面」。"""
    return [
        *_single_agent_checkpoint(),
        message_end(FinishReason.PAUSED, input_tokens=1900, output_tokens=210, cost=_COST),
    ]

def _single_agent_checkpoint_resolved() -> list[SSEEvent]:
    """单聊·检查点【冷路恢复】(② resume)：ask_user(blocking) 落帧暂停后，用户经
    ``POST .../resume`` 拍板续跑——``checkpoint_resolved`` 清掉 pendingInteraction、status 从
    paused 回 running，回合继续产出并正常 ``end_turn`` 收尾。对照 ``_single_agent_checkpoint``
    （停在 ``checkpoint_required`` 的挂起态）验「同一张 resume 卡在续跑后关闭、回合跑到底」。"""
    return [
        *_single_agent_checkpoint(),
        checkpoint_resolved(checkpoint_id="cp1", decision="continue"),
        content_delta("好，按 A 推进。"),
        content_delta(" 已完成初稿。"),
        message_end(FinishReason.END_TURN, input_tokens=2100, output_tokens=260, cost=_COST),
    ]

def _plan_review_finalized() -> list[SSEEvent]:
    """结构化挂起·计划复核【收口即终止】(②)：delegate ``checkpoint_after`` 落帧后回合以
    ``message_end(finish_reason=paused)`` 收口（对照 ``_plan_review_paused`` 的「停在
    ``plan_review_required``、无 ``message_end``」挂起态）。``status`` 仍 ``paused``、
    ``pendingInteraction`` 仍 plan_review、已完成 r1 仍带 checkpoint 徽标，但 ``finishReason="paused"``
    + ``cost`` 落账。delegate 的 plan_review 对偶，证终止式挂起在多 Agent 图上同样退回单张 resume 卡。"""
    return [
        *_plan_review_paused(),
        message_end(FinishReason.PAUSED, input_tokens=3000, output_tokens=400, cost=_COST),
    ]

def _proposal_pick_checkpoint() -> list[SSEEvent]:
    """单聊·方案挑选卡 (ask_user card=proposal_pick)：阻塞挂起，intent=proposal_pick，
    恰好 1 个 choice 单选 + options 2–6。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("有三条可行路线，请挑一条："),
        checkpoint_required(
            checkpoint_id="cp_pick",
            conversation_id=_CONV,
            question="选哪条方案推进？\n三条路线成本与风险不同。",
            questions=[
                {
                    "id": "q0",
                    "prompt": "选哪条方案？",
                    "kind": "choice",
                    "multiple": False,
                    "default": "",
                    "options": [
                        {"label": "方案 A：快速原型", "detail": "一周内可验证"},
                        {"label": "方案 B：稳妥重构", "detail": "两周，债务更少"},
                        {"label": "方案 C：外包试点", "recommended": True},
                    ],
                }
            ],
            intent="proposal_pick",
        ),
        message_end(FinishReason.PAUSED, input_tokens=1800, output_tokens=120, cost=_COST),
    ]


def _ask_user_shape_reject_then_pick() -> list[SSEEvent]:
    """单聊·方案挑选卡先被结构校验拒绝、模型自纠正后改对：ask_user(card=proposal_pick) 误塞
    多题 → 结构校验拒绝（tool_use_start + tool_use_end status=error）→ 自纠正后重发单题单选挂起。

    棘轮：ask_user 是交互原语，由 checkpoint/ask 标记代言（MARKER_STANDIN_TOOLS），其
    tool_use_start/end 都【不落 captain 工具步】——所以这条「可自纠正的结构校验失败」既不
    残留 open 工具行、也不外泄红色工具错误给用户；golden.process 仅有 checkpoint 标记。"""
    return [
        message_start("m1", conversation_id=_CONV),
        # 误用：card=proposal_pick 却塞了 2 个 question — 结构校验拒绝（模型可自纠正）。
        tool_use_start(
            "ask_reject",
            "ask_user",
            {
                "card": "proposal_pick",
                "message": "做官网前先定几件事",
                "questions": [{"prompt": "选整体风格?"}, {"prompt": "选配色?"}],
            },
        ),
        tool_use_end(
            "ask_reject",
            "ask_user",
            success=False,
            output=(
                "card=proposal_pick 要求恰好 1 个 question（kind=choice、multiple=false、"
                "options 2–6 个候选方案）。两条出路：要问多个【不同】问题 → 去掉 card；"
                "同一决策的候选方案 → 合并成 1 个 question 的多个 options。"
            ),
        ),
        # 自纠正后重发单题单选 → 正常挂起（模型自写了 question，引导句留在气泡）。
        content_delta("有三条可行路线，请挑一条："),
        checkpoint_required(
            checkpoint_id="cp_pick",
            conversation_id=_CONV,
            question="选哪条方案推进？\n三条路线成本与风险不同。",
            questions=[
                {
                    "id": "q0",
                    "prompt": "选哪条方案？",
                    "kind": "choice",
                    "multiple": False,
                    "default": "",
                    "options": [
                        {"label": "方案 A：快速原型", "detail": "一周内可验证"},
                        {"label": "方案 B：稳妥重构", "detail": "两周，债务更少"},
                        {"label": "方案 C：外包试点", "recommended": True},
                    ],
                }
            ],
            intent="proposal_pick",
        ),
        message_end(FinishReason.PAUSED, input_tokens=1900, output_tokens=140, cost=_COST),
    ]


def _risk_ack_checkpoint() -> list[SSEEvent]:
    """单聊·风险确认卡 (ask_user card=risk_ack)：阻塞挂起，intent=risk_ack，
    恰好 1 个 choice 多选 + options 1–10。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("落地前请勾选要一并处理的风险："),
        checkpoint_required(
            checkpoint_id="cp_risk",
            conversation_id=_CONV,
            question="哪些风险要在本轮处理？\n未勾选的项将记入后续 backlog。",
            questions=[
                {
                    "id": "q0",
                    "prompt": "勾选要处理的风险",
                    "kind": "choice",
                    "multiple": True,
                    "default": "",
                    "options": [
                        {
                            "label": "[高] 密钥轮换",
                            "detail": "生产密钥仍是默认值，泄露即全库失守",
                            "recommended": True,
                        },
                        {"label": "[中] 备份校验", "detail": "近 30 天备份未做恢复演练"},
                        {"label": "回滚演练"},
                    ],
                }
            ],
            intent="risk_ack",
        ),
        message_end(FinishReason.PAUSED, input_tokens=1900, output_tokens=140, cost=_COST),
    ]


def _presentation_kickoff_format_options() -> list[SSEEvent]:
    """退役棘轮：场面 format_options 已拆除；普通短澄清挂起（无场面字段）。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("开始前确认课件交付形态："),
        checkpoint_required(
            checkpoint_id="cp_fmt",
            conversation_id=_CONV,
            question="这份课件用哪种交付形态？\n演讲 / PPT 意图：缺信息靠短问，错了再改。",
            intent="decision",
        ),
        checkpoint_resolved(
            checkpoint_id="cp_fmt",
            decision="continue",
            note="选 PowerPoint",
        ),
        content_delta("好，按 PowerPoint（.pptx）推进。"),
        message_end(FinishReason.END_TURN, input_tokens=1800, output_tokens=140, cost=_COST),
    ]


def _carrier_means_consult_smartart_boundary() -> list[SSEEvent]:
    """载体/手段顾问·能力边界前置（种子 A）：用户要 Word 图形 SmartArt 组织图 → 先诚实说做不到
    图形 SmartArt，再短 ask 推荐可交替代（交互 HTML / PPT / Word 文本层级）并保留「仍要 Word
    文字版」；禁先答笼统「可以」再缩水。对照 ``presentation_kickoff_format_options``（普通短澄清）
    与 ``proposal_pick_checkpoint``（recommended 选项卡）。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta(
            "Word 里做不出带框连线的图形 SmartArt 组织架构图；"
            "我这边能交的是文本层级 docx、PPT 连线版，或可折叠交互 HTML。"
        ),
        checkpoint_required(
            checkpoint_id="cp_carrier_smartart",
            conversation_id=_CONV,
            question=(
                "组织架构图用哪种可交形态？\n"
                "能力边界前置：图形 SmartArt 做不到；推荐更适合的载体，"
                "仍可坚持 Word 文字版。"
            ),
            questions=[
                {
                    "id": "q0",
                    "prompt": "选哪种可交形态？",
                    "kind": "choice",
                    "multiple": False,
                    "default": "交互 HTML 组织图（可折叠）",
                    "options": [
                        {
                            "label": "交互 HTML 组织图（可折叠）",
                            "detail": "宽树也能看全",
                            "recommended": True,
                        },
                        {
                            "label": "PPT 带框连线版",
                            "detail": "可打开编辑的图形页",
                        },
                        {
                            "label": "Word 文本层级版（仍要 Word）",
                            "detail": "缩进/表格文字版，非图形 SmartArt",
                        },
                    ],
                }
            ],
            intent="decision",
        ),
        message_end(FinishReason.PAUSED, input_tokens=1800, output_tokens=140, cost=_COST),
    ]


def _carrier_means_consult_html_org_tree() -> list[SSEEvent]:
    """载体/手段顾问·次优载体/框架锁定（种子 B）：用户要极宽组织树「只翻译、框架不变、存 HTML」
    → 短对齐提示静态 1:1 难看全，推荐折叠/分区等更好呈现，并保留「仍按原样 HTML」；非盲跟开做。
    对照 ``carrier_means_consult_smartart_boundary``（能力边界）与 ``proposal_pick_checkpoint``。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta(
            "这棵组织树很宽，静态 HTML 1:1 照搬几乎看不全；"
            "更适合可折叠树或按部门分区，也可仍按原样 HTML。"
        ),
        checkpoint_required(
            checkpoint_id="cp_carrier_html_tree",
            conversation_id=_CONV,
            question=(
                "组织树 HTML 用哪种呈现？\n"
                "次优载体短对齐：框架可保，呈现建议改；坚持原样静态 HTML 亦可。"
            ),
            questions=[
                {
                    "id": "q0",
                    "prompt": "选哪种 HTML 呈现？",
                    "kind": "choice",
                    "multiple": False,
                    "default": "可折叠树 HTML",
                    "options": [
                        {
                            "label": "可折叠树 HTML",
                            "detail": "保留层级，默认收拢便于看全",
                            "recommended": True,
                        },
                        {
                            "label": "按部门分区多页 HTML",
                            "detail": "框架不变，分页降低横向溢出",
                        },
                        {
                            "label": "仍按原样静态 HTML 1:1",
                            "detail": "只翻译、不改框架，接受裁切/滚动",
                        },
                    ],
                }
            ],
            intent="decision",
        ),
        message_end(FinishReason.PAUSED, input_tokens=1800, output_tokens=140, cost=_COST),
    ]


def _organize_plan_checkpoint() -> list[SSEEvent]:
    """单聊·整理方案卡 (ask_user card=organize_plan)：阻塞挂起，intent=organize_plan，
    恰好 1 个 choice 多选 + options 1–50（原路径→新路径）。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("请确认整理方案（取消勾选即剔除）："),
        checkpoint_required(
            checkpoint_id="cp_org",
            conversation_id=_CONV,
            question="按下列方案整理桌面？\n确认后按方案批量执行，不再二次弹审批。",
            questions=[
                {
                    "id": "q0",
                    "prompt": "整理项",
                    "kind": "choice",
                    "multiple": True,
                    "default": "",
                    "options": [
                        {
                            "label": "发票.pdf → 财务/发票.pdf",
                            "op": "move",
                            "source": "external/desk/发票.pdf",
                            "destination": "external/desk/财务/发票.pdf",
                        },
                        {
                            "label": "新建 财务/",
                            "op": "mkdir",
                            "path": "external/desk/财务",
                        },
                    ],
                }
            ],
            intent="organize_plan",
        ),
        message_end(FinishReason.PAUSED, input_tokens=1900, output_tokens=140, cost=_COST),
    ]


def _team_preview_finalized() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。团队预审向量：多 Agent 首委派在 run_plan 后、
    首波前以 ``message_end(finish_reason=paused)`` 收口。投影无 team_preview；
    有 run_plan 时过程时间线为 content + team。与 plan_review 波间闸门分离。"""
    agents = [
        {
            "id": "w1",
            "role": "调研",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "撰写",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研方案", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "写初稿", "depends_on": ["r1"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "调研"}, {"role": "撰写"}], "coordinate": False},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="构建 X",
            agents=agents,
            runs=plan_runs,
        ),
        message_end(FinishReason.PAUSED, input_tokens=1200, output_tokens=80, cost=_COST),
    ]


def _debate_team_preview_finalized() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。辩论向量：顶层 debate 在主持人循环启动前挂起收口。"""
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来组织一场辩论。"),
        tool_use_start(
            "db1",
            "debate",
            {
                "motion": "该不该上四天工作制？",
                "form": "debate",
                "sides": [
                    {"key": "pro", "name": "正方", "stance": "应推广"},
                    {"key": "con", "name": "反方", "stance": "暂缓"},
                ],
                "thorough": True,
            },
        ),
        message_end(FinishReason.PAUSED, input_tokens=800, output_tokens=40, cost=_COST),
    ]


def _debate_team_preview_research_first() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。本向量保留 research_first 回灌旧路径（不开赛）。

    不再发挂起 / resolve 事件对；回灌文案与不开赛行为不变。
    """
    from agentcore.runtime.kickoff.research_first import research_first_tool_result

    motion = "该不该上四天工作制？"
    refeed = research_first_tool_result(motion=motion, user_message="")
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来组织一场辩论。"),
        tool_use_start(
            "db1",
            "debate",
            {
                "motion": motion,
                "form": "debate",
                "sides": [
                    {"key": "pro", "name": "正方", "stance": "应推广"},
                    {"key": "con", "name": "反方", "stance": "暂缓"},
                ],
                "thorough": True,
            },
        ),
        tool_use_end("db1", "debate", success=True, output=refeed),
        content_delta("好的，我先挂多视角调研。"),
        message_end(FinishReason.END_TURN, input_tokens=900, output_tokens=60, cost=_COST),
    ]


def _debate_team_preview_resolved_adjust() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。adjust 路径：不开赛，意见回灌 CEO；无辩手 / 主持人 start。"""
    from agentcore.runtime.kickoff.adjust_guidance import format_kickoff_adjust_result

    note = "先改命题再辩"
    refeed = format_kickoff_adjust_result(primitive="debate", note=note)
    return [
        *_debate_team_preview_finalized()[:-1],
        tool_use_end("db1", "debate", success=True, output=refeed),
        content_delta("好的，我按你的意见改开赛方案再提交。"),
        message_end(FinishReason.END_TURN, input_tokens=900, output_tokens=60, cost=_COST),
    ]


def _debate_team_preview_research_first_recommended() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。不再点亮 research_first_recommended；辩论工具后 paused 收口。"""
    motion = "LV 案模拟法庭：一审判决是否过重？"
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来组织一场辩论。"),
        tool_use_start(
            "db1",
            "debate",
            {
                "motion": motion,
                "form": "debate",
                "sides": [
                    {"key": "plaintiff", "name": "原告", "stance": "判决过重"},
                    {"key": "defendant", "name": "被告", "stance": "判决适当"},
                ],
                "thorough": True,
            },
        ),
        message_end(FinishReason.PAUSED, input_tokens=800, output_tokens=40, cost=_COST),
    ]


def _debate_team_preview_resolved_continue() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。主持人循环已开赛（协作图有 runs）。

    对照 ``debate_team_preview_finalized``（paused、无开工卡）与 ``team_preview_resolved_continue``
    （delegate 已 start）：本向量停在首轮正反陈述进行中，不收场——前端可离线预览
    「已开赛」协作图态。
    """
    cap, mod = "captain1", "debate_mod1"
    pro_run, con_run = f"{mod}_r1_pro", f"{mod}_r1_con"
    motion = "该不该上四天工作制？"
    mod_agents, mod_runs = _moderator_agents_runs(mod, cap, f"主持正反辩论：{motion}")
    debater_agents = _pro_con_debater_agents()
    debater_runs = _pro_con_debater_runs(
        mod,
        pro_run,
        con_run,
        pro_task="论证应推广四天工作制",
        con_task="论证暂缓推行四天工作制",
    )
    return [
        *_debate_team_preview_finalized()[:-1],
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary=f"正反辩论：{motion}",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        debate_round_started(
            execution_id="exec1",
            moderator_run_id=mod,
            round_no=1,
            focus="生产力与员工福祉的取舍",
            cross_exam_enabled=True,
            opening="这场要定的是该不该上四天工作制，先从产出与休息的权衡切入。",
        ),
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(pro_run, "d_pro", parent_run_id=mod),
        run_output_delta(
            pro_run,
            "d_pro",
            "正方：四天制可降低倦怠、提升专注产出，试点企业已有可量化对照。",
        ),
        run_started(con_run, "d_con", parent_run_id=mod),
        run_output_delta(
            con_run,
            "d_con",
            "反方：服务业与协作密集岗位难以压缩工时，仓促推广会抬高成本与排班摩擦。",
        ),
        # 无 message_end：回合仍在辩论中（status=running），协作图可见主持人+正反双方。
    ]


def _debate_team_preview_model_override_continue() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。开赛沿用预分配 id；人盖模型写在 run，不从事件投影 veto。"""
    mod = "debate_mod1"
    return [
        *_debate_team_preview_finalized()[:-1],
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="正反辩论：该不该上四天工作制？",
            agents=[{"id": mod, "role": "主持人", "thinking": True}],
            runs=[
                {
                    "id": mod,
                    "agent_id": mod,
                    "task": "主持正反辩论：该不该上四天工作制？",
                    "depends_on": [],
                }
            ],
        ),
        run_started(mod, mod, parent_run_id="captain1"),
        # 无 message_end：人盖后已开赛（协作图可见主持人）；辩手模型写在 config，不在本帧展开。
    ]


def _team_preview_resolved_continue() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。首波开跑到 end_turn；投影无 team_preview。"""
    return [
        *_team_preview_finalized()[:-1],
        run_started("r1", "w1"),
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
        run_started("r2", "w2"),
        run_completed(
            "r2",
            "w2",
            output_summary="初稿完成",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队完成"),
        content_delta("团队已交付。"),
        message_end(FinishReason.END_TURN, input_tokens=3000, output_tokens=400, cost=_COST),
    ]


def _team_preview_resolved_adjust() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。adjust 路径：不授权、不开工，意见回灌 CEO；无 worker start。"""
    from agentcore.runtime.kickoff.adjust_guidance import format_kickoff_adjust_result

    note = "人太多，改成两人调研"
    refeed = format_kickoff_adjust_result(primitive="delegate", note=note)
    return [
        *_team_preview_finalized()[:-1],
        run_skipped("r1", "w1", reason="abort"),
        run_skipped("r2", "w2", reason="abort"),
        tool_use_end("dc1", "delegate", success=True, output=refeed),
        content_delta("好的，我按你的意见改方案再提交开工。"),
        message_end(FinishReason.END_TURN, input_tokens=1600, output_tokens=120, cost=_COST),
    ]


def _team_preview_resolved_adjust_pre_ttft() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。adjust 后 CEO 续跑、尚未吐首 token。

    生产冷恢复增量（服务端已核实）：``message_start``（复用原助手 id，无 ``full_replay``）
    → 每个未跑 worker 一条 ``run_skipped(abort)``
    → ``tool_use_end``（修订引导回灌）→ ``run_started(captain)``（复用同一 captain_run_id）。
    本向量停在 captain ``run_started`` 之后、首个 ``reasoning_delta`` / ``content_delta`` /
    ``tool_use_start`` 之前，供逐帧回放看见该窗口的气泡。

    前缀不含 ``message_end(paused)``：与 sibling adjust 同构。挂起收口后再用无
    ``full_replay`` 的同 id ``message_start`` 续折，oracle 会保留 ``finish_reason=paused``，
    无法观察「续跑中、无新正文」中间态。``run_plan`` 按生产注入 captain 根节点，使续跑
    ``run_started`` 能点亮同一 captain run。
    """
    from agentcore.runtime.delegate.plan_events import captain_card, captain_run
    from agentcore.runtime.kickoff.adjust_guidance import format_kickoff_adjust_result

    cap = "c1"
    note = "人太多，改成两人调研"
    refeed = format_kickoff_adjust_result(primitive="delegate", note=note)
    return [
        message_start("m1", conversation_id=_CONV),
        run_started(cap, cap, kind="captain"),
        content_delta("我来安排团队。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "调研"}, {"role": "撰写"}], "coordinate": False},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="构建 X",
            agents=[
                captain_card(cap),
                {"id": "w1", "role": "调研", "thinking": True},
                {"id": "w2", "role": "撰写", "thinking": True},
            ],
            runs=[
                captain_run(cap),
                {"id": "r1", "agent_id": "w1", "task": "调研方案", "depends_on": []},
                {"id": "r2", "agent_id": "w2", "task": "写初稿", "depends_on": ["r1"]},
            ],
        ),
        message_start("m1", conversation_id=_CONV),
        run_skipped("r1", "w1", reason="abort"),
        run_skipped("r2", "w2", reason="abort"),
        tool_use_end("dc1", "delegate", success=True, output=refeed),
        run_started(cap, cap, kind="captain"),
    ]


def _team_preview_revised_card() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。adjust 后修订成 1 人再挂起（不再出第二张开工卡）。"""
    from agentcore.runtime.kickoff.adjust_guidance import format_kickoff_adjust_result

    note = "人太多，改成一个人做"
    refeed = format_kickoff_adjust_result(primitive="delegate", note=note)
    first = _team_preview_finalized()[:-1]
    return [
        *first,
        tool_use_end("dc1", "delegate", success=True, output=refeed),
        content_delta("按你的意见改成一人，请再确认。"),
        tool_use_start(
            "dc2",
            "delegate",
            {"tasks": [{"role": "写手", "task": "一个人做完"}]},
        ),
        run_plan(
            execution_id="exec2",
            plan_type="multi_agent",
            task_summary="一人交付",
            agents=[{"id": "w3", "role": "写手", "thinking": True}],
            runs=[{"id": "r3", "agent_id": "w3", "task": "一个人做完", "depends_on": []}],
        ),
        message_end(FinishReason.PAUSED, input_tokens=1800, output_tokens=140, cost=_COST),
    ]


def _team_preview_exclude_one_continue() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。排除一人后续跑 — 否决字段不再从事件投影。

    两岗无依赖，可合法排除 r2；首波仅 r1 开跑到 end_turn。
    非法依赖排除（仍被 depends_on 引用）→ API 422 单测，本向量不表达拒答。
    """
    agents = [
        {"id": "w1", "role": "调研", "thinking": True},
        {"id": "w2", "role": "校对", "thinking": True},
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研方案", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "校对摘要", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "调研"}, {"role": "校对"}], "coordinate": False},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="并行双岗",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
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
        tool_use_end("dc1", "delegate", success=True, output="团队完成（已排除校对岗）"),
        content_delta("已排除校对岗，调研交付。"),
        message_end(FinishReason.END_TURN, input_tokens=2400, output_tokens=280, cost=_COST),
    ]


def _team_preview_tighten_write_continue() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。收紧写盘后续跑 — write_capability_overrides 不再从事件投影。"""
    agents = [
        {"id": "w1", "role": "调研", "thinking": True},
        {"id": "w2", "role": "撰写", "thinking": True},
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研方案", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "写初稿", "depends_on": ["r1"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "调研"}, {"role": "撰写"}], "coordinate": False},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="构建 X",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
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
        run_started("r2", "w2"),
        run_completed(
            "r2",
            "w2",
            output_summary="初稿完成（仅文字）",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队完成"),
        content_delta("撰写岗已收紧为仅文字，团队已交付。"),
        message_end(FinishReason.END_TURN, input_tokens=3000, output_tokens=400, cost=_COST),
    ]


def _team_preview_model_override_continue() -> list[SSEEvent]:
    """开工卡事件对已退役、不再发。人盖模型写在 run_completed，不再从事件投影 modelOverrides。"""
    agents = [
        {"id": "w1", "role": "调研", "thinking": True},
        {"id": "w2", "role": "撰写", "thinking": True},
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研方案", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "写初稿", "depends_on": ["r1"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "调研"}, {"role": "撰写"}], "coordinate": False},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研撰写",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
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
        run_started("r2", "w2"),
        run_completed(
            "r2",
            "w2",
            output_summary="初稿完成（人指定 Pro）",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队完成"),
        content_delta("撰写岗已改用 Pro，团队已交付。"),
        message_end(FinishReason.END_TURN, input_tokens=3000, output_tokens=400, cost=_COST),
    ]


def _execution_completed_gate_still_pending() -> list[SSEEvent]:
    """执行完成 + 门禁仍挂起：队员已齐、``execution_completed(status=completed)`` 已落，
    但 CEO 仍以 ``checkpoint_required`` 阻塞，并以 ``message_end(paused)`` 收口。

    钉死 TurnStatus 判定序（桌面 / oracle）：finishReason → error → gate pending →
    running。``execution_completed`` 只校正协作图节点，不得把回合判成 completed
    （手机 fold 曾把该帧放在优先级最高位，挂起卡被吞成已完成）。
    """
    agents = [{"id": "w1", "role": "调研", "thinking": True}]
    plan_runs = [{"id": "r1", "agent_id": "w1", "task": "出方案", "depends_on": []}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("团队已交付，请确认是否按此方案推进。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="出方案",
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
        execution_completed(
            execution_id="exec1",
            conversation_id=_CONV,
            completed=1,
            total=1,
            status="completed",
            host_turn_id="m1",
        ),
        checkpoint_required(
            checkpoint_id="cp-after-exec",
            conversation_id=_CONV,
            question="按此方案推进吗？\n团队已交付方案。",
            intent="decision",
        ),
        message_end(FinishReason.PAUSED, input_tokens=2200, output_tokens=180, cost=_COST),
    ]


def _decision_then_kill() -> list[SSEEvent]:
    """决策后杀进程：settlement 已落、无终态 → fold 无 pending gate，status=running。

    验收（回合恢复状态机收口）：重启投影不得出现待授权卡；对应 UI 救火「继续」（已决策·执行中断）。
    """
    return [
        *_team_preview_finalized()[:-1],
        run_started("r1", "w1"),
        # 杀进程：无 message_end / 无新 gate
    ]


def _decision_then_second_gate_then_kill() -> list[SSEEvent]:
    """决策后执行中二次挂起再杀：只投影新决策卡，旧 settlement 不产生中断态双显。"""
    return [
        *_team_preview_finalized()[:-1],
        run_started("r1", "w1"),
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
        checkpoint_required(
            checkpoint_id="cp-second",
            conversation_id=_CONV,
            question="调研结论你认吗？\n二次挂起",
            intent="decision",
        ),
        message_end(FinishReason.PAUSED, input_tokens=2200, output_tokens=180, cost=_COST),
    ]


VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    "approval_paused": ("审批：approval_required 暂停（无 message_end）", _approval_paused),
    "approval_resolved_continue": ("审批：通过后继续到 end_turn", _approval_resolved_continue),
    "plan_review_paused": ("结构化挂起：plan_review_required 暂停", _plan_review_paused),
    "plan_review_resolved_continue": ("结构化挂起：放行后跑完下游", _plan_review_resolved_continue),
    "plan_review_finalized": ("结构化挂起：计划复核收口即终止（②，plan_review_required→message_end(paused)，单一冷路 resume）", _plan_review_finalized),
    "team_preview_finalized": ("开工卡已退役、不再发事件对：首波前 paused 收口（无开工卡）", _team_preview_finalized),
    "team_preview_resolved_continue": ("开工卡已退役、不再发事件对：首波开跑到 end_turn", _team_preview_resolved_continue),
    "team_preview_resolved_adjust": (
        "开工卡已退役、不再发事件对：adjust 不授权不开工、意见回灌 CEO（无 worker start）",
        _team_preview_resolved_adjust,
    ),
    "team_preview_resolved_adjust_pre_ttft": (
        "开工卡已退役、不再发事件对：adjust 后 CEO 续跑、尚未吐首 token（停在 captain run_started）",
        _team_preview_resolved_adjust_pre_ttft,
    ),
    "team_preview_revised_card": (
        "开工卡已退役、不再发事件对：adjust 后修订成 1 人再挂起（不再出第二张卡）",
        _team_preview_revised_card,
    ),
    "team_preview_exclude_one_continue": (
        "开工卡已退役、不再发事件对：排除一人后续跑（否决字段不再从事件投影）",
        _team_preview_exclude_one_continue,
    ),
    "team_preview_tighten_write_continue": (
        "开工卡已退役、不再发事件对：收紧写盘后续跑（否决字段不再从事件投影）",
        _team_preview_tighten_write_continue,
    ),
    "team_preview_model_override_continue": (
        "开工卡已退役、不再发事件对：人盖模型写在 run，不从事件投影 modelOverrides",
        _team_preview_model_override_continue,
    ),
    "execution_completed_gate_still_pending": (
        "冲突：execution_completed(completed) + 门禁仍挂起 → status=paused（不得判成已完成）",
        _execution_completed_gate_still_pending,
    ),
    "decision_then_kill": (
        "恢复收口：决策后杀进程 → fold 无待授权、status=running（已决策·执行中断 / 救火继续）",
        _decision_then_kill,
    ),
    "decision_then_second_gate_then_kill": (
        "恢复收口：决策后二次挂起再杀 → 仅新决策卡 pending，无中断态双显",
        _decision_then_second_gate_then_kill,
    ),
    "debate_team_preview_finalized": ("开工卡已退役、不再发事件对：辩论主持人循环前挂起收口", _debate_team_preview_finalized),
    "debate_team_preview_research_first": (
        "开工卡已退役、不再发事件对：research_first 回灌旧路径（不开赛）",
        _debate_team_preview_research_first,
    ),
    "debate_team_preview_resolved_adjust": (
        "开工卡已退役、不再发事件对：辩论 adjust 不开赛、意见回灌 CEO（无辩手 start）",
        _debate_team_preview_resolved_adjust,
    ),
    "debate_team_preview_research_first_recommended": (
        "开工卡已退役、不再发事件对：不再点亮 research_first_recommended（paused 收口）",
        _debate_team_preview_research_first_recommended,
    ),
    "debate_team_preview_resolved_continue": (
        "开工卡已退役、不再发事件对：开赛后主持人+正反已 start（协作图可见）",
        _debate_team_preview_resolved_continue,
    ),
    "debate_team_preview_model_override_continue": (
        "开工卡已退役、不再发事件对：开赛沿用预分配 run_id（不从事件投影 veto）",
        _debate_team_preview_model_override_continue,
    ),
    "single_agent_checkpoint": ("单聊：检查点 ask_user(blocking) 在时间线原位落 checkpoint 标记 + 暂停", _single_agent_checkpoint),
    "single_agent_checkpoint_finalized": ("单聊：检查点收口即终止（②，checkpoint_required→message_end(paused)，单一冷路 resume）", _single_agent_checkpoint_finalized),
    "single_agent_checkpoint_resolved": ("单聊：检查点 ask_user(blocking) 经 resume 续跑（checkpoint_resolved 清挂起→跑到 end_turn）", _single_agent_checkpoint_resolved),
    "proposal_pick_checkpoint": ("单聊：方案挑选卡 ask_user(card=proposal_pick) 挂起（intent=proposal_pick）", _proposal_pick_checkpoint),
    "ask_user_shape_reject_then_pick": (
        "单聊：方案挑选卡误塞多题被结构校验拒绝→自纠正改对（校验失败不落工具步、不外泄红错）",
        _ask_user_shape_reject_then_pick,
    ),
    "risk_ack_checkpoint": ("单聊：风险确认卡 ask_user(card=risk_ack) 挂起（intent=risk_ack）", _risk_ack_checkpoint),
    "organize_plan_checkpoint": ("单聊：整理方案卡 ask_user(card=organize_plan) 挂起（intent=organize_plan）", _organize_plan_checkpoint),
    "presentation_kickoff_format_options": (
        "退役棘轮：场面 format_options 已拆除；普通短澄清挂起",
        _presentation_kickoff_format_options,
    ),
    "carrier_means_consult_smartart_boundary": (
        "载体/手段顾问：Word SmartArt 能力边界前置 → 诚实做不到 + recommended 可交替代 ask（含仍要 Word 文字版）",
        _carrier_means_consult_smartart_boundary,
    ),
    "carrier_means_consult_html_org_tree": (
        "载体/手段顾问：极宽组织树 HTML 次优载体短对齐 → 推荐折叠/分区 + 保留仍按原样 HTML",
        _carrier_means_consult_html_org_tree,
    ),
}
