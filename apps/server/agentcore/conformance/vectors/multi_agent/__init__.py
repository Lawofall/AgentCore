"""Conformance vector builders — multi-agent orchestration scenarios.

See ``vectors/__init__.py`` for the aggregated ``VECTORS`` registry.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.runtime.events import SSEEvent

from .async_delivery import (
    _multi_agent_execution_detached_completed,
    _multi_agent_execution_detached_harvest_settle,
)
from .auto_folder import _multi_agent_auto_folder_created
from .batch4_hardening import (
    _multi_agent_incremental_preview_badge,
    _multi_agent_merge_race_secondary_delegate,
    _multi_agent_run_completed_gaps,
    _multi_agent_stop_gate_run_frames,
    _multi_agent_timeout_hard_gaps,
)
from .browser import _multi_agent_browser_login_pending, _multi_agent_browser_session
from .context import _multi_agent_captain_context, _multi_agent_received_context
from .coordinate import (
    _multi_agent_coordinate,
    _multi_agent_coordination_wait,
)
from .cross_turn_append import (
    _multi_agent_cross_turn_append,
    _multi_agent_cross_turn_live_prev,
)
from .delegate import (
    _multi_agent_delegate,
    _multi_agent_worker_deliverable_reset,
    _multi_agent_worker_failed_debrief,
    _multi_agent_worker_failed_format,
    _multi_agent_worker_output_reset,
    _multi_agent_worker_process_timeline,
    _multi_agent_worker_tool,
)
from .delivery import (
    _multi_agent_ceo_rate_limit_paused,
    _multi_agent_delivery_status_partial,
    _multi_agent_export_docx_artifacts,
    _multi_agent_pptx_promised_md_only,
    _multi_agent_worker_rate_limit_partial,
)
from .escalation import (
    _multi_agent_blocking_escalate,
    _multi_agent_blocking_escalate_multi,
    _multi_agent_blocking_escalate_pending,
    _multi_agent_blocking_escalate_timeout,
    _multi_agent_ceo_arbitrate_escalate,
    _multi_agent_ceo_arbitrate_escalate_via_user,
    _multi_agent_escalation,
)
from .interjection import (
    _multi_agent_solo_coordinate_interjection,
    _multi_agent_user_interjection_delegate_append,
    _multi_agent_user_interjection_failed,
    _multi_agent_user_interjection_handled,
    _multi_agent_user_interjection_queued,
    _multi_agent_user_interjection_with_attachments,
    _multi_agent_user_interjection_with_mentions,
)
from .mlr_debate_acts import _multi_agent_mlr_debate_acts
from .mlr_debate_witness import _multi_agent_mlr_debate_witness
from .multi_lens_research import _multi_agent_multi_lens_research
from .research_ledger import _multi_agent_research_ledger
from .revision import (
    _multi_agent_lead_peer_mixed_overlap,
    _multi_agent_lead_subplan_bind_replan,
    _multi_agent_lead_subplan_scope_steer,
    _multi_agent_multi_batch,
    _multi_agent_multi_batch_disjoint,
    _multi_agent_plan_revised,
    _multi_agent_redelegate_continuation,
    _multi_agent_revision,
)
from .run_control import (
    _multi_agent_run_redirect_cold_fallback,
    _multi_agent_run_redirect_hot,
    _multi_agent_run_redirect_ignored,
    _multi_agent_run_skipped_cascade,
    _multi_agent_run_stop_cancels_workers,
    _multi_agent_run_user_stop_worker,
)
from .run_phase import _multi_agent_run_phase
from .same_turn_mlr_debate import _multi_agent_same_turn_mlr_debate
from .stage_card import (
    _multi_agent_stage_card_orphaned,
    _multi_agent_stage_card_start_debate,
)
from .two_act_lv import _multi_agent_two_act_lv

VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    "multi_agent_execution_detached_completed": (
        "异步团队产出投递：execution_detached → 后台 run_completed → execution_completed"
        "（v1 fold no-op；覆盖 DURABLE 处置门禁）",
        _multi_agent_execution_detached_completed,
    ),
    "multi_agent_execution_detached_harvest_settle": (
        "批次4·detached settle：execution_detached → execution_completed → 收口回合终稿",
        _multi_agent_execution_detached_harvest_settle,
    ),
    "multi_agent_stop_gate_run_frames": (
        "批次4·stop 门：message_end(cancelled) 后仍到达 run_cancelled 级联帧，fold 如实收口",
        _multi_agent_stop_gate_run_frames,
    ),
    "multi_agent_incremental_preview_badge": (
        "批次4·增量组队：首批仍在跑时二次 delegate 叠加（不压运行态）",
        _multi_agent_incremental_preview_badge,
    ),
    "multi_agent_merge_race_secondary_delegate": (
        "批次4·merge：同回合二次 delegate 同 execution_id 生长合并",
        _multi_agent_merge_race_secondary_delegate,
    ),
    "multi_agent_run_completed_gaps": (
        "批次4·批次3新面：run_completed.gaps 软放行缺口一等化",
        _multi_agent_run_completed_gaps,
    ),
    "multi_agent_timeout_hard_gaps": (
        "批次4·批次3新面：超时硬收尾 gaps.reason=worker_timeout",
        _multi_agent_timeout_hard_gaps,
    ),
    "multi_agent_user_interjection_handled": (
        "协调插话入图：user_interjection received→injected→addressed，折到 userInterjections",
        _multi_agent_user_interjection_handled,
    ),
    "multi_agent_user_interjection_queued": (
        "协调插话转排队：user_interjection received→injected→queued（同 id 保最新）+ queue_user_message",
        _multi_agent_user_interjection_queued,
    ),
    "multi_agent_user_interjection_failed": (
        "协调插话失败：user_interjection received→injected→failed（同 id 保最新）",
        _multi_agent_user_interjection_failed,
    ),
    "multi_agent_user_interjection_with_attachments": (
        "协调带附件插话：user_interjection(received) 携带 attachments 元数据 → userInterjections",
        _multi_agent_user_interjection_with_attachments,
    ),
    "multi_agent_user_interjection_with_mentions": (
        "协调带点名插话：user_interjection(received) 携带 agent_mentions 软芯片 → userInterjections",
        _multi_agent_user_interjection_with_mentions,
    ),
    "multi_agent_solo_coordinate_interjection": (
        "单 worker+协调：非阻塞 kickoff → 执行期插话 → cancel_worker 终止"
        "（无 team_synthesis_preview；钉 solo 进协调 + 插话可达）",
        _multi_agent_solo_coordinate_interjection,
    ),
    "multi_agent_user_interjection_delegate_append": (
        "协调插话入图：user_interjection received→injected→CEO 二次 delegate 追加"
        "→addressed（note=已在本回合据此调整团队）",
        _multi_agent_user_interjection_delegate_append,
    ),
    "multi_agent_auto_folder_created": (
        "裸聊写盘自动建文件夹（§5.4）：auto_folder_created DURABLE → autoFolder 投影"
        "（对话内不渲染落点条；不挂起回合）",
        _multi_agent_auto_folder_created,
    ),
    "multi_agent_delegate": ("多 Agent：委派 2 队员，runs 树 + 进度 + 总账", _multi_agent_delegate),
    "multi_agent_browser_session": (
        "浏览器：worker 用 browser_*（navigate→snapshot→click→screenshot），"
        "每步 tool_use_end.display 携 kind:browser 契约 + 关键帧引用，折入 run.process",
        _multi_agent_browser_session,
    ),
    "multi_agent_browser_login_pending": (
        "浏览器：worker browser_* 后 escalate(browser_login=true) pending——"
        "登录卡 + 自动揭示右坞壳（shoot）",
        _multi_agent_browser_login_pending,
    ),
    "multi_agent_cross_turn_append": (
        "跨回合协作图续接：m1 建图完成 → m2 新 execution_id + prev_execution_id=exec1 → "
        "追加批收口；进度分母只含本图；不再 graph_append / host_message_id divert",
        _multi_agent_cross_turn_append,
    ),
    "multi_agent_cross_turn_live_prev": (
        "跨回合上一张仍在后台跑：m1 execution_detached 后 r1 未完成 → m2 新 execution_id "
        "+ prev_execution_id=exec1；新人只在新图，投影 runs 不含 r1",
        _multi_agent_cross_turn_live_prev,
    ),
    "multi_agent_multi_lens_research": (
        "多 Agent·多视角深度调研幕1：delegate → 4 透镜并行流式 → "
        "汇总分析师 debrief.motion_card → CEO 呈报建议开辩（开辩入口 stage_card）",
        _multi_agent_multi_lens_research,
    ),
    "multi_agent_mlr_debate_acts": (
        "批A2·两幕链：幕1 MLR multi_agent + 幕2 debate 新图 + prev"
        "（act-2/anchor=synthesizer；authorized_by=preview；不再 divert）",
        _multi_agent_mlr_debate_acts,
    ),
    "multi_agent_same_turn_mlr_debate": (
        "同回合两幕同一张图：一条消息先 MLR 再 debate，单 execution_id、acts 两幕、无 prev",
        _multi_agent_same_turn_mlr_debate,
    ),
    "multi_agent_mlr_debate_witness": (
        "批D1·证人模式：幕1 MLR + 幕2 辩论新图+prev；证人席位 + witness_exam 答问进台账"
        "（答问 run 挂辩论幕席位下，continues=席位根）",
        _multi_agent_mlr_debate_witness,
    ),
    "multi_agent_two_act_lv": (
        "批R2·幕级 LOD 验收：LV 案量级两幕（幕1 MLR 含法律子队 + 幕2 辩论新图+prev 含证人/补派两轮），"
        "约 18 节点，供内嵌 + 全屏协作图幕摘要卡链 + 聚焦幕验单屏可读",
        _multi_agent_two_act_lv,
    ),
    "multi_agent_stage_card_start_debate": (
        "批B·推进卡：stage_card_required → start_debate → 幕2 新图+prev"
        "（authorized_by=stage_card）",
        _multi_agent_stage_card_start_debate,
    ),
    "multi_agent_stage_card_orphaned": (
        "批B·推进卡：stage_card_required → 下回合未调 debate/未起 MLR → "
        "收尾 interaction_orphaned",
        _multi_agent_stage_card_orphaned,
    ),
    "multi_agent_research_ledger": (
        "多 Agent·调研台账 P2：worker 引 #rN → evidence_ledger 全量；"
        "citations_event=仅引用集（含 weak+tier 徽标）；未引用命中只留台账痕迹",
        _multi_agent_research_ledger,
    ),
    "multi_agent_coordinate": (
        "刷新重建（P2）：协调模式 team_synthesis_preview DURABLE → teamSynthesisPreview（同 key 保最新）",
        _multi_agent_coordinate,
    ),
    "multi_agent_coordination_wait": (
        "协调等待 UX：coordination_wait(waiting=true) EPHEMERAL → StatusStrip 只报 n/m"
        "（成员细节在协作图节点；无长文案 / 内联成员列表 / 「协调等待」徽标）",
        _multi_agent_coordination_wait,
    ),
    "multi_agent_delivery_status_partial": (
        "交付状态结构化：delivery_status DURABLE → deliveryStatus（同 execution_id 保最新；"
        "已交付文件 + 缺口 + bind_local_folder 行动项随卡重建）",
        _multi_agent_delivery_status_partial,
    ),
    "multi_agent_export_docx_artifacts": (
        "交付台账·导出件：md + 自报 derived_from 的 docx 双双进 delivery_status.artifacts"
        "（导出件自成 artifacts 行并带 derived_from；终稿路径可点）",
        _multi_agent_export_docx_artifacts,
    ),
    "multi_agent_pptx_promised_md_only": (
        "选 pptx 却只落 md/脚本：delivery_status=partial 可见缺口；"
        "假「PPT 已可打开」经 finish_guard content_reset 回炉为诚实终稿",
        _multi_agent_pptx_promised_md_only,
    ),
    "multi_agent_worker_rate_limit_partial": (
        "委派·限流：worker 落盘 3 个 CSV 后撞 429（仅一帧 run_failed + LLM_RATE_LIMIT/"
        "retryable；未 attested 秒数不上线）；delivery_status=partial 认 3 产物；CEO 汇总再 429 "
        "把 delegate 交代渲染成回复（outcome=partial，正文非空）",
        _multi_agent_worker_rate_limit_partial,
    ),
    "multi_agent_ceo_rate_limit_paused": (
        "委派·限流暂停：worker 一帧 run_failed（LLM_RATE_LIMIT/retryable）；delegate 已闭合；"
        "CEO 429 → message_end(finish=paused, outcome=paused)；无 *_required、无系统收口用户行",
        _multi_agent_ceo_rate_limit_paused,
    ),
    "multi_agent_worker_failed_debrief": (
        "多 Agent：worker 未过契约（run_failed）但调 handoff 交了交接简报——失败节点也 surface debrief",
        _multi_agent_worker_failed_debrief,
    ),
    "multi_agent_worker_failed_format": (
        "多 Agent：worker 结构/格式闸失败 → run_failed.failure_kind=format（协作图「格式未过」）",
        _multi_agent_worker_failed_format,
    ),
    "multi_agent_run_skipped_cascade": (
        "多 Agent·未执行收口：级联跳过 run_skipped(cascade) + graceful abort run_skipped(abort)，"
        "节点折 skipped「未执行」而非永久排队",
        _multi_agent_run_skipped_cascade,
    ),
    "multi_agent_run_phase": (
        "多 Agent·worker 活动相位：run_phase(thinking/tool/waiting_children/winding_down) "
        "+ pending queued + run_skipped；winding_down 粘性",
        _multi_agent_run_phase,
    ),
    "multi_agent_run_redirect_ignored": (
        "多 Agent·跑一半改方向·忽略路径：改方向来不及应用（r1 确定性失败），忽略+接受走审计/REST 带外，wire 投影保持干净（r1 failed、并行 r2 completed、1/2、无幻影重跑节点）",
        _multi_agent_run_redirect_ignored,
    ),
    "multi_agent_run_stop_cancels_workers": (
        "多 Agent·整轮 stop：in-flight worker 均 run_cancelled(reason=stop)，无热/冷 follow-up 节点，回合 cancelled",
        _multi_agent_run_stop_cancels_workers,
    ),
    "multi_agent_run_user_stop_worker": (
        "多 Agent·只停这项工作：run_cancelled(reason=user_stop)，无热/冷 follow-up，兄弟完成，"
        "delegate 成功返回、回合 end_turn（非整轮 cancelled）",
        _multi_agent_run_user_stop_worker,
    ),
    "multi_agent_run_redirect_hot": (
        "多 Agent·跑一半改方向·热续写：已有 partial 产出 → cancel(reason=redirect) + continue_run 修订子节点（r1 cancelled、r1_rev1 completed、r2 completed、无 _redir）",
        _multi_agent_run_redirect_hot,
    ),
    "multi_agent_run_redirect_cold_fallback": (
        "多 Agent·跑一半改方向·冷诚实回落：空产出 → cancel(reason=redirect) + _redir 接手（r1 cancelled、r1_redir completed+replacesRunId=r1、r2 completed）",
        _multi_agent_run_redirect_cold_fallback,
    ),
    "multi_agent_worker_tool": ("多 Agent：worker 工具调用 + run_tool_progress 实时态", _multi_agent_worker_tool),
    "multi_agent_worker_process_timeline": (
        "多 Agent：worker per-run process 时间线交错（思考→工具→正文），live/回放同源",
        _multi_agent_worker_process_timeline,
    ),
    "multi_agent_worker_output_reset": (
        "多 Agent：交付前核验回炉 worker 对偶 run_output_reset 丢弃违规版 worker 草稿、保留思考、重写修正版",
        _multi_agent_worker_output_reset,
    ),
    "multi_agent_worker_deliverable_reset": (
        "多 Agent·交付正文只留最终交付：worker 调非终止工具前的旁白 run_output_reset 清掉（落点在工具后）、保留思考、只留最终交付",
        _multi_agent_worker_deliverable_reset,
    ),
    "multi_agent_revision": ("多 Agent：同人续派（continues_run_id 合成节点）", _multi_agent_revision),
    "multi_agent_redelegate_continuation": (
        "多 Agent：delegate 带 continue_from_run_id 的同批续派（计划内节点 + continues_run_id）",
        _multi_agent_redelegate_continuation,
    ),
    "multi_agent_plan_revised": ("多 Agent：自主再绑定「计划已调整」轻痕迹（plan_revised 折 bind/steer 到节点 revised）", _multi_agent_plan_revised),
    "multi_agent_lead_subplan_bind_replan": (
        "多 Agent·嵌套 lead 在自己子计划上晚定稿续跑（受监督子计划 B：同 execution_id 合并子图 + lead 自主 replan bind 折到子节点）",
        _multi_agent_lead_subplan_bind_replan,
    ),
    "multi_agent_lead_subplan_scope_steer": (
        "多 Agent·嵌套 lead 据子队员 scope 偏离操舵子计划（受监督子计划 B 自底向上：run_escalation 折子节点 + lead 自主 replan steer 折子节点）",
        _multi_agent_lead_subplan_scope_steer,
    ),
    "multi_agent_lead_peer_mixed_overlap": (
        "多 Agent·嵌套 lead + 平级同名角色混合（反模式）：引擎不拒单；lead 子队挂 L1、平级挂根，"
        "嵌套由 depth+parent_run_id 表达（委派默认开、无 opt-in 开关），双路径仍可投影",
        _multi_agent_lead_peer_mixed_overlap,
    ),
    "multi_agent_multi_batch": ("多 Agent：同回合两批 delegate（合并 + 累计进度）", _multi_agent_multi_batch),
    "multi_agent_multi_batch_disjoint": (
        "多 Agent：同回合两批 delegate、跨批无 depends_on（两坨独立任务线；第二批中途追加）",
        _multi_agent_multi_batch_disjoint,
    ),
    "multi_agent_escalation": ("多 Agent：worker 升级实时可见（run_escalation 折到节点 escalations，非阻塞）", _multi_agent_escalation),
    "multi_agent_blocking_escalate": ("多 Agent：阻塞式求决策 答复路径（escalation_required→pending→resolved，回合不 paused）", _multi_agent_blocking_escalate),
    "multi_agent_blocking_escalate_timeout": ("多 Agent：阻塞式求决策 墙钟超时（escalation_resolved status=timed_out，按假设续跑）", _multi_agent_blocking_escalate_timeout),
    "multi_agent_blocking_escalate_pending": ("多 Agent：阻塞式求决策 进行中（escalation_required 后挂起，回合仍 running、非 paused）", _multi_agent_blocking_escalate_pending),
    "multi_agent_blocking_escalate_multi": ("多 Agent：阻塞式求决策 同一 worker 串行多次升级（多升级 escalations[]，逐条结算）", _multi_agent_blocking_escalate_multi),
    "multi_agent_ceo_arbitrate_escalate": (
        "多 Agent·协调：CEO 仲裁阻塞 escalate（awaiting=ceo → resolve 直裁，arbitrated_by=ceo）",
        _multi_agent_ceo_arbitrate_escalate,
    ),
    "multi_agent_ceo_arbitrate_escalate_via_user": (
        "多 Agent·协调：CEO 经用户转交后再 resolve（arbitrated_by=ceo, via_user=true）",
        _multi_agent_ceo_arbitrate_escalate_via_user,
    ),
    "multi_agent_received_context": ("多 Agent：收到的上下文（run_context 三通道 + 依赖块溯源/保真度）", _multi_agent_received_context),
    "multi_agent_captain_context": ("多 Agent：CEO 收到的上下文路由回合级（captain 节点 receivedContext 恒空）+ worker 折到节点", _multi_agent_captain_context),
}
