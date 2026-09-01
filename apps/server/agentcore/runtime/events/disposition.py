"""事件持久化处置的单一权威源（处置单一源 / 持久化优化 A+）。

每个 :class:`~agentcore.runtime.events.types.EventType` 必须在 :data:`EVENT_DISPOSITION`
里有且仅有一条处置声明，三选一：

- ``DURABLE``   —— 落 ``turn_journal``（事实源），reload 由 journal fold 重放。
  :data:`DURABLE_EVENT_TYPES` 即由此**派生**（取所有 DURABLE），
  ``journal_config._JOURNAL_EVENT_TYPES`` 复用它，不再手维护第二份清单。
- ``DERIVED``   —— 信息经**专用列 / 其它投影**持久化（非 journal allow-list），reload 时重建
  （如 content_delta→Message.content、title_generated→Conversation.title）。
- ``EPHEMERAL`` —— **有意不持久化**，reload 后按设计丢失（传输控制帧 / 进度心跳 /
  客户端工具请求 / 进程内交互态）。这不是「漏」，而是被显式记录的取舍。

**为什么要这张表**：历史上「哪些事件落库」散落在 allow-list（``_JOURNAL_EVENT_TYPES``）+
各投影列 + 前端 fold 的 default 分支里，新增事件极易**静默遗漏**（不声明处置也能编译通过、
也能上线，只是重载后内容凭空消失）。本表把处置收敛到一处，并由
``tests/test_event_disposition.py`` 的两道门禁守护：

1. **穷尽门禁**：``set(EventType) == set(EVENT_DISPOSITION)`` —— 新增事件不声明处置即 CI 红。
2. **DURABLE 覆盖门禁**：每个 DURABLE 必须被某条 conformance 向量覆盖，或在测试的
   ``DURABLE_VECTOR_WAIVERS`` 里显式豁免（带理由）—— 挡住「落库但从没测过重放」。

改动本表前请先读本 docstring；调整某事件处置时，同步更新其对应的落库列 / 投影 / 前端 fold。
"""

from __future__ import annotations

from enum import StrEnum

from agentcore.runtime.events.types import EventType


class Disposition(StrEnum):
    """一个事件相对于「回合重载」的持久化归属。"""

    DURABLE = "durable"
    DERIVED = "derived"
    EPHEMERAL = "ephemeral"


# EventType → (处置, 一行理由)。穷尽覆盖全部 EventType（由测试强制）。
EVENT_DISPOSITION: dict[EventType, tuple[Disposition, str]] = {
    # ---- DURABLE：落 turn_journal，reload 由 fold 重放（= 现 _JOURNAL_EVENT_TYPES） ----
    EventType.RUN_PLAN: (Disposition.DURABLE, "团队图/单体计划——重放团队结构与过程时间线的锚"),
    EventType.GRAPH_APPEND: (
        Disposition.DURABLE,
        "跨回合同图追加锚点——追加回合 process 标记；生长帧续写宿主 turn_id journal",
    ),
    EventType.RUN_STARTED: (Disposition.DURABLE, "某 run 起始——重放该节点的开始"),
    EventType.RUN_CONTEXT: (Disposition.DURABLE, "派发给 run 的上下文/依赖——重放收到的上下文"),
    EventType.RUN_COMPLETED: (Disposition.DURABLE, "run 完成（含 message_final）——重放产出/发言"),
    EventType.RUN_FAILED: (Disposition.DURABLE, "run 失败——重放失败态与原因"),
    EventType.RUN_CANCELLED: (
        Disposition.DURABLE,
        "run 中途取消（redirect/user_stop/stop）——重放停态，避免假 working",
    ),
    EventType.RUN_SKIPPED: (
        Disposition.DURABLE,
        "run 未执行（cascade/abort）——重放「未执行」，避免假排队中",
    ),
    EventType.RUN_PROGRESS: (Disposition.DURABLE, "run 阶段进度里程碑——重放过程节拍"),
    EventType.BATCH_METRICS: (Disposition.DURABLE, "调度埋点量化——journal 重放；产品不展示"),
    EventType.DEBATE_RESULT: (Disposition.DURABLE, "辩论最终裁决——重放结论"),
    EventType.TOOL_USE_START: (Disposition.DURABLE, "工具调用开始——重放工具时间线条目"),
    EventType.TOOL_USE_END: (Disposition.DURABLE, "工具调用结束（结果）——重放工具结果"),
    EventType.CHECKPOINT_REQUIRED: (Disposition.DURABLE, "检查点挂起（耐久帧）——reload 重现待裁决卡"),
    EventType.CHECKPOINT_RESOLVED: (Disposition.DURABLE, "检查点已裁决——重放裁决结果"),
    EventType.PLAN_REVIEW_REQUIRED: (Disposition.DURABLE, "计划复核挂起（耐久帧）——reload 重现复核卡"),
    EventType.PLAN_REVIEW_RESOLVED: (Disposition.DURABLE, "计划复核已裁决——重放裁决"),
    EventType.STAGE_CARD_REQUIRED: (
        Disposition.DURABLE,
        "阶段推进卡登记（跨回合耐久）——reload 重现开辩/补调研决议入口",
    ),
    EventType.STAGE_CARD_RESOLVED: (Disposition.DURABLE, "阶段推进卡已裁决——重放裁决"),
    EventType.PLAN_REVISED: (Disposition.DURABLE, "自主再绑定「计划已调整」轻痕迹——重放"),
    EventType.ESCALATION_REQUIRED: (Disposition.DURABLE, "升级请求（单一发射者）——重放升级"),
    EventType.ESCALATION_RESOLVED: (Disposition.DURABLE, "升级已处理——重放结果"),
    EventType.APPROVAL_REQUIRED: (
        Disposition.DURABLE,
        "审批门挂起——reload 重现待答卡（提问确认交互统一 P1）",
    ),
    EventType.APPROVAL_RESOLVED: (
        Disposition.DURABLE,
        "审批门裁决——reload 重放已答态（提问确认交互统一 P1）",
    ),
    EventType.INTERACTION_ORPHANED: (
        Disposition.DURABLE,
        "热路交互失效——reload 翻「已失效」不可点态（提问确认交互统一 P1）",
    ),
    EventType.DEBATE_ROUND_STARTED: (
        Disposition.DURABLE,
        "辩论轮次开场——hydrateFromJournal / fold 重建辩论室进行态（P2 处置重对账）",
    ),
    EventType.DEBATE_ROUND: (
        Disposition.DURABLE,
        "辩论单轮叙事——hydrateFromJournal / fold 重建逐轮焦点/小结/裁判（P2）",
    ),
    EventType.DEBATE_PRETRIAL_STARTED: (
        Disposition.DURABLE,
        "庭前取证开场——赛事页庭前区块进行态；fast/约定文档充分可带 skip_reason",
    ),
    EventType.DEBATE_PRETRIAL_ORDERS: (
        Disposition.DURABLE,
        "庭前主辩点单——各方取证任务 + 对称取证员数量",
    ),
    EventType.DEBATE_PRETRIAL_COMPLETED: (
        Disposition.DURABLE,
        "庭前取证收口——done/skipped/degraded + evidence_ledger_delta",
    ),
    EventType.TURN_WARNING: (
        Disposition.DURABLE,
        "回合前软门禁提示——用户可见；runs 投影 → toMessage 重现横幅（P2）",
    ),
    EventType.AUTO_FOLDER_CREATED: (
        Disposition.DURABLE,
        "裸聊写盘自动建文件夹——DURABLE；对话内不再渲染落点条，文件夹进「我的文件」",
    ),
    EventType.TEAM_SYNTHESIS_PREVIEW: (
        Disposition.DURABLE,
        "协调模式团队进展预览——同 key 保最新由前端 fold 保证；刷新后 StatusStrip 可重建（P2）",
    ),
    EventType.DELIVERY_STATUS: (
        Disposition.DURABLE,
        "交付状态结构化对账（已交付/缺口/元数据）——同 execution_id 保最新；供 finish_guard 与只合回产物读路径（用户面无验收大卡、无聊天流产物清单卡）",
    ),
    EventType.USER_INTERJECTION: (
        Disposition.DURABLE,
        "运行中用户插话（经典+协调共用）——同 interjection_id 保最新 status"
        "（协调 received→injected→addressed|queued|failed；"
        "经典 received→injected|queued|failed）；刷新可回看",
    ),
    EventType.TURN_QUEUED: (
        Disposition.EPHEMERAL,
        "同对话 FIFO 排队 ack（发送即有流）——传输态；drain 后同连接续流，reload 无需重放",
    ),
    EventType.TURN_QUEUE_STARTED: (
        Disposition.EPHEMERAL,
        "时间线用户泡入场（正文在帧上）；reload 靠 REST",
    ),
    EventType.TURN_QUEUE_CANCELLED: (
        Disposition.EPHEMERAL,
        "同对话排队项取消 ack——传输态；多端清 UI，reload 无需重放",
    ),
    EventType.RESUME_DEFERRED: (
        Disposition.EPHEMERAL,
        "冷 resume × live deferred ack——传输态；槽空后同连接续跑，reload 无需重放",
    ),
    EventType.RESUME_SETTLED: (
        Disposition.EPHEMERAL,
        "冷 resume 幂等成功 ack（帧已被消费）——传输态；决策事实本身已在 journal，reload 无需重放",
    ),
    EventType.EXECUTION_DETACHED: (
        Disposition.DURABLE,
        "执行转后台（回合收口/CEO 提前收口仍有队员在跑）——重放「团队后台继续」态",
    ),
    EventType.EXECUTION_COMPLETED: (
        Disposition.DURABLE,
        "后台执行终态——重放完成态；不再自动新开思考轮",
    ),
    # ---- DERIVED：经专用列 / 其它投影持久化，reload 时重建（非 journal allow-list） ----
    EventType.CONTENT_DELTA: (Disposition.DERIVED, "正文流——最终态落 Message.content 列"),
    EventType.REASONING_DELTA: (Disposition.DERIVED, "思考流——最终态落 Message.reasoning_content 列"),
    EventType.CITATIONS: (Disposition.DERIVED, "联网来源——落 Message.citations 列"),
    EventType.EVIDENCE_LEDGER: (
        Disposition.DERIVED,
        "回合调研台账/检索痕迹——落 Message.evidence_ledger 列（不占 citations_event；对称辩论 O1）",
    ),
    EventType.MESSAGE_END: (
        Disposition.DERIVED,
        "收尾（token/finish/cost）——落 Message.usage + journal turn_end；cost 回写 Message.cost 列",
    ),
    EventType.ERROR: (
        Disposition.DERIVED,
        "回合错误——落 journal turn_end + usage.status=failed（不完整回合持久化）",
    ),
    EventType.TITLE_GENERATED: (Disposition.DERIVED, "回合后标题——回写 Conversation.title 列"),
    EventType.RUN_OUTPUT_DELTA: (Disposition.DERIVED, "worker 正文流——由 message_final 事实合成重放"),
    EventType.RUN_REASONING_DELTA: (Disposition.DERIVED, "worker 思考流——由 message_final 事实合成重放"),
    EventType.RUN_ESCALATION: (
        Disposition.DURABLE,
        "非阻塞 raised 升级（统一时间线二期 D6）——重放 raised 轻行 + 节点 ⚠️ 徽标 + 时间线标记",
    ),
    EventType.RUN_ESCALATION_GATE: (
        Disposition.DERIVED,
        "Escalation Gate 方案层判定实时信号——耐久记录并入 RunState.escalations / escalate 通道",
    ),
    # ---- EPHEMERAL：有意不持久化，reload 后按设计丢失（显式取舍，非漏） ----
    EventType.MESSAGE_START: (Disposition.EPHEMERAL, "回合起始控制帧——reload 即已开始，无需重放"),
    EventType.CONTENT_RESET: (Disposition.EPHEMERAL, "流内纠正（丢弃已流内容重来）——重载以最终列为准"),
    EventType.RUN_OUTPUT_RESET: (Disposition.EPHEMERAL, "run 流内纠正——重载以 message_final 为准"),
    EventType.TURN_SAVED: (Disposition.EPHEMERAL, "落库确认控制帧——reload 本身即已保存态"),
    EventType.TOOL_PROGRESS: (Disposition.EPHEMERAL, "工具参数流式心跳——传输态，工具已完成"),
    EventType.TOOL_USE_PROGRESS: (Disposition.EPHEMERAL, "工具执行阶段心跳——传输态，工具已完成"),
    EventType.WORKSPACE_LOCK_WAIT: (
        Disposition.EPHEMERAL,
        "同 folder 写锁短等——传输态；waiting 进出；不得静默等锁，reload 无需重放",
    ),
    EventType.COORDINATION_WAIT: (
        Disposition.EPHEMERAL,
        "CEO 协调等待心跳——传输态（waiting true/false）；reload 时等待已结束或由 live SSE 重挂",
    ),
    EventType.RUN_TOOL_PROGRESS: (Disposition.EPHEMERAL, "run 工具进度心跳——传输态"),
    EventType.RUN_PHASE: (
        Disposition.EPHEMERAL,
        "worker 活动相位（thinking/tool/waiting_children/winding_down）——传输态；"
        "queued/skipped 走 RunStatus；reload 后由 status 兜底",
    ),
    EventType.WORKSPACE_OP_REQUIRED: (Disposition.EPHEMERAL, "客户端工具请求（请求/响应交换，非回合内容）"),
    EventType.BOARD_OP_REQUIRED: (Disposition.EPHEMERAL, "白板客户端工具请求（请求/响应交换，非回合内容）"),
    EventType.BOARD_READ_REQUIRED: (Disposition.EPHEMERAL, "白板栅格化读取客户端工具请求（非回合内容）"),
    EventType.DESKTOP_NOTIFY_REQUIRED: (
        Disposition.EPHEMERAL,
        "桌面系统通知客户端工具请求（非回合内容）",
    ),
    EventType.EXTERNAL_MOUNT_READONLY_REQUIRED: (
        Disposition.EPHEMERAL,
        "区外只读静默挂载客户端工具请求（非回合内容）",
    ),
    EventType.MCP_OP_REQUIRED: (
        Disposition.EPHEMERAL,
        "本机 MCP Client 工具请求（stdio 回填，请求/响应交换，非回合内容）",
    ),
    EventType.HOST_OP_REQUIRED: (
        Disposition.EPHEMERAL,
        "本机 Host 客户端工具请求（非回合内容）",
    ),
    EventType.HANDOFF_SNAPSHOT_DONE: (Disposition.EPHEMERAL, "接管快照控制帧——传输态"),
    EventType.HANDOFF_JOB_STARTED: (Disposition.EPHEMERAL, "接管任务启动控制帧——传输态"),
    EventType.HANDOFF_APPLY_DONE: (Disposition.EPHEMERAL, "接管应用完成控制帧——传输态"),
    EventType.WORKSPACE_SNAPSHOT_DONE: (
        Disposition.EPHEMERAL,
        "回合后自动备份成功——传输态，清失败横幅",
    ),
    EventType.WORKSPACE_SNAPSHOT_FAILED: (
        Disposition.EPHEMERAL,
        "回合后自动备份失败——传输态 toast/横幅，不挡回合",
    ),
    EventType.BROWSER_LIVE_FRAME: (
        Disposition.EPHEMERAL,
        "团队浏览器直播帧（base64 jpeg）——D13 旁路 SSE 通道推送，永不落 journal/history",
    ),
    EventType.BROWSER_LIVE_STATUS: (
        Disposition.EPHEMERAL,
        "团队浏览器直播状态（started/no_session/session_closed）——D13 旁路 SSE，永不落 journal",
    ),
}


DURABLE_EVENT_TYPES: frozenset[EventType] = frozenset(
    event for event, (disposition, _reason) in EVENT_DISPOSITION.items()
    if disposition is Disposition.DURABLE
)
"""所有 DURABLE 事件——``_JOURNAL_EVENT_TYPES`` 的单一来源。"""
