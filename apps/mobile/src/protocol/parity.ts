// 对等对账门禁 (parity gate) · 登记表 —— 桌面/协议新增「该上手机」的交互面时，漏改手机 →
// 门禁自动响，而非悄悄漂回去 (cross-platform-frontend.mdc:「手机 = 桌面 − 物理做不到的能力」)。
//
// 三锚（数据在此，校验在 parity.check.ts，挂在现有 mobile conformance / CI job 上）：
//   锚 A · 协议事件（编译期响）：{@link EVENT_PARITY} 是 Record<SSEEventType, ParityEntry>。
//     SSEEventType 是后端单一源自动生成的穷尽联合 (eventTypes.generated.ts)、两端 fold 已
//     `assertNever` 它。再做成 Record → 后端加新事件、`pnpm gen:types` 重生成联合后，缺键即
//     `tsc` 失败（CI mobile typecheck 门禁），直到你给出手机对等裁决。与 fold 的 assertNever 同
//     款棘轮。
//   锚 B · 桌面交互面（测试期响）：{@link DESKTOP_CHAT_PARITY} 列举 apps/desktop/.../components/
//     chat 顶层 .tsx + `ask/` 子树（提问 intent 专用卡）的对等裁决；parity.check.ts 扫对照面、
//     断言每个 .tsx 都在表里有键（桌面新建一面 → conformance 失败直到分类）。捕获非事件通道喂
//     的面（如 ③ 记忆卡走 REST、后台任务卡、@提及…）。其余 chat 子目录（debate/…）仍不入表免抖动。
//   锚 C · 桌面页面（测试期响）：{@link DESKTOP_PAGE_PARITY} 列举 apps/desktop/.../pages 下每个
//     .tsx 页面（含子目录）的对等裁决；parity.check.ts 递归扫该目录、断言每个 .tsx 都在表里有键
//     （桌面新建一页 → conformance 失败直到分类）。接住整页 / route 级漂移（白板/工具箱/手册/成员…）。
//
// 边界（诚实）：门禁强制的是「有没有给出对等裁决」，不验证手机实现是否正确/已接线——那是
// typecheck（死/没接线代码）、conformance（fold 漂移）、可视化自检的活。三层分层互补，本表只补
// 「整面漏掉/没分类」这一段。符合 protocol-conformance.mdc「组件/chrome 不进巡检」：此处不巡检
// 实现，只强制一条裁决记录存在，两端 chrome 仍自由分叉。

import type { SSEEventType } from "@agentcore/contract-types";

/** 一条对等裁决。
 *  - `ported`：手机已覆盖（`surface` 给出手机落点：组件/位置）。
 *  - `simplified`：已知缺口 / 有意精简（`reason` 说明精简了什么/为何；门禁仍绿，报告会列出）。
 *  - `impossible`：手机物理做不到（`reason` 说明绑了哪种本地/桌面专属能力）。
 *  - `internal`：非用户面（纯协议管线/派生/渲染叶，`reason` 一句点明）。 */
export type ParityVerdict = "ported" | "simplified" | "impossible" | "internal";

export interface ParityEntry {
  verdict: ParityVerdict;
  /** 手机落点（ported 必填；simplified 视情可填）：组件名或位置。 */
  surface?: string;
  /** 理由（simplified / impossible / internal 必填；ported 可省，surface 已自证）。 */
  reason?: string;
}

/** 锚 A · 协议事件 → 手机对等裁决。`Record<SSEEventType, …>` 强制穷尽：缺键 = tsc 失败。 */
export const EVENT_PARITY: Record<SSEEventType, ParityEntry> = {
  // —— CEO 内联时间线：正文 / 思考 / 工具 / 引用 ——
  content_delta: { verdict: "ported", surface: "AssistantView 时间线 · 正文" },
  content_reset: {
    verdict: "ported",
    surface: "fold · 清正文（仅 reason=finish_guard 折 rework chip）",
  },
  reasoning_delta: { verdict: "ported", surface: "AssistantView · 思考块" },
  tool_use_start: {
    verdict: "ported",
    surface:
      "AssistantView · 工具步 (CEO 自身调用) + RunDetail · 队员工具明细 (run_id 侧，extractRunToolCalls)",
  },
  tool_use_end: {
    verdict: "ported",
    surface:
      "AssistantView · 工具步 (CEO 自身调用) + RunDetail · 队员工具明细 (run_id 侧，extractRunToolCalls)",
  },
  tool_use_progress: {
    verdict: "ported",
    surface:
      "AssistantView · 工具步执行阶段 (CEO, extractToolPhases) + TeamView · 队员节点 (worker run_id, extractWorkerToolPhases)",
  },
  citations: { verdict: "ported", surface: "AssistantView · 来源" },
  evidence_ledger: {
    verdict: "ported",
    surface:
      "fold → ProjectedTurn.evidenceLedger → ChatPage/Preview Markdown；history=toMessageDetail(evidence_ledger)；辩论 `#eN` 仍走 extractEvidenceLedger→TeamView（O7 无 Popover）",
  },

  // —— 多 Agent 团队 ——
  run_plan: {
    verdict: "ported",
    surface:
      "TeamView · 本回合完整协作图；可选 prev_execution_id → ProcessTimeline「续自上一张图」文案行",
  },
  graph_append: {
    verdict: "ported",
    surface:
      "AssistantView · 旧 journal 跨回合同图追加锚点（fold → process.graph_append）；新路径用 run_plan.prev_execution_id + 本回合 TeamView",
  },
  coordination_wait: {
    verdict: "ported",
    surface:
      "TeamView · 团队条 n/m（旁路 extractCoordinationWait，不进 ProjectedTurn）",
  },
  workspace_lock_wait: {
    verdict: "internal",
    reason:
      "同 folder 写锁短等 EPHEMERAL；桌面空气泡「等待工作区…」（不得静默等锁）；手机 fold no-op",
  },
  run_started: { verdict: "ported", surface: "TeamView" },
  run_phase: {
    verdict: "ported",
    surface:
      "TeamView · worker 活动相位（thinking/tool/waiting_children/winding_down；queued=pending；skipped=status）",
  },
  run_context: {
    verdict: "ported",
    surface:
      "AssistantMessageFooter · 更多 → 收到的上下文 (CEO 侧 captainContext) + RunDetail · 队员收到的上下文 (worker 侧 receivedContext)",
  },
  run_output_delta: {
    verdict: "ported",
    surface: "TeamView · 队员输出预览 + RunDetail · 输出全文",
  },
  run_output_reset: {
    verdict: "ported",
    surface: "fold · 清 worker 草稿（仅 reason=finish_guard 折 rework 步）",
  },
  run_reasoning_delta: {
    verdict: "ported",
    surface: "RunDetail · 队员思考全文",
  },
  run_tool_progress: { verdict: "ported", surface: "TeamView · 队员工具进度" },
  run_completed: {
    verdict: "ported",
    surface: "TeamView + RunDetail (交接简报 / 资源用量 / 时长 / 关系)",
  },
  run_failed: { verdict: "ported", surface: "TeamView" },
  run_cancelled: {
    verdict: "ported",
    surface: "TeamView · 跑一半改方向 / 整轮停止",
  },
  run_skipped: {
    verdict: "ported",
    surface: "TeamView · 未执行（级联跳过 / graceful abort）",
  },
  run_progress: {
    verdict: "internal",
    reason: "进度由 run 状态派生（仅时间线计数标记），无独立面",
  },
  plan_revised: { verdict: "ported", surface: "TeamView · 计划已调整 痕迹" },
  run_escalation: {
    verdict: "ported",
    surface: "AssistantView · escalation 时间线标记 (非阻塞轻行)",
  },
  run_escalation_gate: {
    verdict: "internal",
    reason:
      "Escalation Gate 判定实时信号；耐久升级仍走 escalate / RunState.escalations",
  },
  escalation_required: {
    verdict: "ported",
    surface:
      "AssistantView · EscalationAnswer 待你拍板卡 (②)；browser_login → extractEscalationSlots.esc.browserLogin 旁路",
  },
  escalation_resolved: {
    verdict: "ported",
    surface:
      "AssistantView · 升级收束轻行；另一端**的人**拍的 → RemoteSettledCards「已由另一端处理」（answeredByAPerson 排除主管仲裁 / 按假设 / 超时兜底，口径同桌面）",
  },

  // —— 辩论（事件 fold 有面；相对桌面赛事页/站队/掌舵为有意精简）——
  debate_result: {
    verdict: "simplified",
    surface: "DebateView",
    reason:
      "双产物精简复盘；无 DebateArena 赛事页 / 记分牌站队 / SteeringPanel 掌舵卡（介入靠主 composer）",
  },
  debate_round_started: {
    verdict: "simplified",
    surface: "LiveDebateNarrative",
    reason: "叙事线有；无赛事页轮次舞台",
  },
  debate_round: {
    verdict: "simplified",
    surface: "LiveDebateNarrative / DebateView",
    reason: "叙事/交锋有；无赛事页并排舞台与站队",
  },
  debate_pretrial_started: {
    verdict: "simplified",
    surface: "LiveDebateNarrative · 庭前取证",
    reason: "台账轻呈现；无独立庭前任务单 UI（桌面亦已退场热路径）",
  },
  debate_pretrial_orders: {
    verdict: "simplified",
    surface: "LiveDebateNarrative · 庭前取证",
    reason: "同上",
  },
  debate_pretrial_completed: {
    verdict: "simplified",
    surface: "LiveDebateNarrative · 庭前取证 / DebateView",
    reason: "同上",
  },

  // —— 团队便签墙 ——
  team_note_posted: { verdict: "ported", surface: "TeamView · 团队便签" },
  team_synthesis_preview: {
    verdict: "simplified",
    surface: "队员卡 · 产出预览 / RunDetail",
    reason:
      "fold 入库；用户面不画合成行（对齐桌面已收工具栏）。摘要只在队员卡 / RunDetail",
  },
  delivery_status: {
    verdict: "ported",
    surface:
      "FileArtifactsCard · 产物清单（artifacts 主清单；对账档位全静默，partial/blocked 轻提示已撤，两端一致）",
  },
  user_interjection: {
    verdict: "ported",
    surface:
      "ProcessTimeline · user_interjection marker 槽 + InterjectionBubbles 五态（received/injected/addressed/queued/failed；经典+协调；fold → process 钉位 + userInterjections 同 id 保最新，含 agentMentions 点名芯片；旧 journal 无 marker 时 AssistantContent 尾部回退）",
  },
  turn_queued: {
    verdict: "ported",
    surface:
      "ChatPage · turn_queued 为变了信号（发送路径本地即时写条；缺本地 queue_id 则 GET 对账——另一端排的即走这支；排队期不插主时间线用户泡；degraded_from=steer 保留「· 插话暂不可用」）",
  },
  turn_queue_started: {
    verdict: "ported",
    surface:
      "ChatPage · 出队开跑：插主时间线用户泡 + 按 queue_id 清 queuedTurns 条，再 GET 对账余下项序号（fold no-op）",
  },
  turn_queue_cancelled: {
    verdict: "ported",
    surface:
      "ChatPage · 按 queue_id 只清条，再 GET 对账余下项序号（cancel API 成功/404 本地清；另一端取消同走这支；fold no-op）",
  },
  resume_deferred: {
    verdict: "ported",
    surface:
      "ResumeCard ·「放行已记下…」（本端发起）／RemoteSettledCards ·「已由另一端处理」（另一端放行）；ChatPage appendEventToTurn 同连接等待（fold no-op）",
  },
  resume_settled: {
    verdict: "simplified",
    reason:
      "冷 resume 撞上已被消费的挂起帧（EPHEMERAL 幂等成功：200 + 本帧取代旧 404）；不落 journal、不进 ProjectedTurn，两端 fold 均 no-op。手机沿用既有收口：ChatPage resume() 的流正常收尾后 markColdResolved 撤卡，不读 kind/decision/decided_at/turn_status。桌面已用本帧出中性信息态收口条（ResumeSettledNotices）；手机本轮不做 UI，缺口记在锚 B 同名键上",
  },
  execution_detached: {
    verdict: "ported",
    surface:
      "TeamView · 团队条「后台」徽标（旁路 extractExecutionDetached；hydrate 后靠队员仍在跑补）",
  },
  execution_completed: {
    verdict: "ported",
    surface: "ChatPage · getMessages 短延迟刷新拉入 harvest 终稿",
    reason:
      "fold no-op；live SSE 路径触发消息窗刷新（对齐桌面 refreshAfterExecutionCompleted）",
  },

  // —— 阻塞交互（审批热路径 PauseCard；冷恢复 ResumeCard）——
  approval_required: { verdict: "ported", surface: "PauseCard" },
  approval_resolved: {
    verdict: "ported",
    surface:
      "PauseCard 撤卡；另一端点掉的（本端未记账 + 卡正摆着）→ RemoteSettledCards「已由另一端处理」",
  },
  interaction_orphaned: {
    verdict: "ported",
    surface: "静默撤卡（无 OrphanedInteractionCard 墓碑）",
    reason:
      "定案 A：不可操作交互静默消失；store/fold 仍记 orphaned，UI 不渲染灰态卡",
  },
  delegation_authorization_required: {
    verdict: "ported",
    surface: "DelegationAuthorizationCard",
  },
  delegation_authorization_resolved: {
    verdict: "ported",
    surface:
      "DelegationAuthorizationCard 撤卡；另一端点掉的 → RemoteSettledCards「已由另一端处理」",
  },
  checkpoint_required: {
    verdict: "ported",
    surface: "ResumeCard",
    reason:
      "协议折入 ResumeCard；ask intent 专用面已对等（decision/kickoff compose+其他；proposal_pick/risk_ack 行选；organize_plan/daily_review 勾选墙）；本机目录 action 手机不可能履约故禁用",
  },
  checkpoint_resolved: {
    verdict: "simplified",
    surface: "ResumeCard；另一端放行的 → RemoteSettledCards「已由另一端处理」",
    reason: "同上 · 冷路径痕迹；PauseCard 仅审批热路径，不承接 checkpoint",
  },
  plan_review_required: { verdict: "ported", surface: "ResumeCard" },
  plan_review_resolved: {
    verdict: "ported",
    surface: "ResumeCard；另一端放行的 → RemoteSettledCards「已由另一端处理」",
  },
  team_preview_required: { verdict: "ported", surface: "ResumeCard" },
  team_preview_resolved: {
    verdict: "ported",
    surface:
      "ProcessTimeline · team_preview resolved 痕迹（decision=adjust：已调整 · 已交回修订）；另一端放行的 → RemoteSettledCards「已由另一端处理」",
  },
  stage_card_required: { verdict: "ported", surface: "StageCard" },
  stage_card_resolved: {
    verdict: "ported",
    surface: "StageCard；另一端推进的 → RemoteSettledCards「已由另一端处理」",
  },

  // —— 非阻塞提问 (①) ——
  question_posted: {
    verdict: "ported",
    surface: "HangingQuestionBar（pending）／NonBlockingAskCard（resolved）",
  },
  question_resolved: {
    verdict: "ported",
    surface:
      "NonBlockingAskCard 已答 / 已作废；另一端已答 → RemoteSettledCards「已由另一端处理」",
  },

  // —— 跟进推荐（手机有意下线 CEO→用户 chips；事件仍 fold no-op / stopLifecycle 放行）——
  followups_generated: {
    verdict: "simplified",
    reason: "手机有意下线「下一步」chips；事件忽略不展示",
  },
  followups_unavailable: {
    verdict: "simplified",
    reason: "手机有意下线「下一步」chips；不可用标记不再展示",
  },

  // —— 收尾 / 错误 ——
  error: {
    verdict: "ported",
    surface:
      "PausedContinueCard 原因说明（message_end.outcome=paused）／ChatPage · 错误条（其余）",
  },
  message_start: {
    verdict: "internal",
    reason:
      "服务端 message_id 开泡；fold 清正文/process；同 execution_id 暂留 runs/agents（旧 journal 生长）；换 eid 由 run_plan 重置（新 prev 链）",
  },
  message_end: {
    verdict: "ported",
    surface:
      "ChatPage · 收尾 + 回合总账；outcome=paused → PausedContinueCard（已暂停 / 继续）",
  },

  // —— 纯管线 / 派生（非用户面）——
  turn_warning: { verdict: "ported", surface: "ChatPage · 预检警告条" },
  turn_saved: { verdict: "internal", reason: "落库标记，无 UI" },
  title_generated: {
    verdict: "internal",
    reason: "标题经 REST/会话列表呈现，非回合流面",
  },
  tool_progress: {
    verdict: "internal",
    reason: "粗粒度旧进度事件，fold no-op",
  },

  // —— 诊断（桌面 power-user 面）——
  batch_metrics: {
    verdict: "simplified",
    reason: "调度埋点量化仅桌面诊断模式面板；手机无诊断面板 (fold no-op)",
  },

  // —— L3 团队浏览器直播（桌面工作区直播面板 + 接管；手机 BrowserLiveSheet）——
  browser_live_frame: {
    verdict: "ported",
    surface:
      "BrowserLiveSheet（ChatPage 挂载；登录卡「查看直播」开 sheet；ephemeral 侧信道、从不落 turn journal；fold no-op）",
  },
  browser_live_status: {
    verdict: "ported",
    surface:
      "BrowserLiveSheet · 直播通道状态（started / no_session / session_closed）",
  },

  // —— AI 协作白板（桌面画布面，手机无板）——
  board_op_required: {
    verdict: "impossible",
    reason: "AI 协作白板为桌面画布面，手机无板 (fold no-op)",
  },
  board_read_required: {
    verdict: "impossible",
    reason: "同上 · 读板为桌面画布面",
  },
  desktop_notify_required: {
    verdict: "impossible",
    reason: "桌面 OS 通知为 Electron Client Tool，手机无此通道 (fold no-op)",
  },
  external_mount_readonly_required: {
    verdict: "impossible",
    reason:
      "区外只读静默挂载为 Electron Client Tool，手机无此通道 (fold no-op)",
  },
  host_op_required: {
    verdict: "impossible",
    reason: "本机 Host 回填为 Electron Client Tool，手机无此通道 (fold no-op)",
  },
  mcp_op_required: {
    verdict: "impossible",
    reason:
      "本机 MCP stdio 回填为 Electron Client Tool，手机无此通道 (fold no-op)",
  },

  // —— 草稿工作区（本地文件夹）/ 本地↔云交接（物理做不到）——
  workspace_op_required: {
    verdict: "impossible",
    reason: "工作区操作绑本地文件夹；纯云瘦客户端无本地侧",
  },
  handoff_snapshot_done: {
    verdict: "impossible",
    reason: "本地↔云交接（后台任务桥）的本地侧，手机无本地",
  },
  handoff_job_started: {
    verdict: "impossible",
    reason: "同上 · 后台任务桥本地侧",
  },
  handoff_apply_done: {
    verdict: "impossible",
    reason: "同上 · 把云端改动合并回本地磁盘，手机无本地",
  },
  auto_folder_created: {
    verdict: "ported",
    surface:
      "AutoFolderNoticeCard / FileArtifactsCard · 落点告知 + 改名 + 跳转我的文件",
  },
  workspace_snapshot_done: {
    verdict: "simplified",
    reason: "云回合后自动备份成功信号；手机暂无快照面板，fold no-op",
  },
  workspace_snapshot_failed: {
    verdict: "simplified",
    reason: "云回合后自动备份失败信号；手机暂无 toast/快照面板，fold no-op",
  },

  // —— AI 小镇模拟（桌面 MVP，手机无模拟面）——
  "sim.agent_action": {
    verdict: "impossible",
    reason: "AI 小镇模拟仅桌面 MVP，手机无模拟面 (fold no-op)",
  },
  "sim.agent_state": {
    verdict: "impossible",
    reason: "同上 · 居民状态同步",
  },
  "sim.interaction": {
    verdict: "impossible",
    reason: "同上 · 居民交互气泡/交易",
  },
  "sim.tick_started": {
    verdict: "impossible",
    reason: "同上 · 模拟 tick 开始",
  },
  "sim.tick_ended": {
    verdict: "impossible",
    reason: "同上 · 模拟 tick 结束",
  },
  "sim.tick_frame": {
    verdict: "impossible",
    reason: "同上 · 模拟 tick 帧快照",
  },
  "sim.world_event": {
    verdict: "impossible",
    reason: "同上 · 世界事件",
  },
  "sim.show.affection_shift": {
    verdict: "impossible",
    reason: "AI 恋综观测仅桌面/Unity 客户端，手机无模拟面 (fold no-op)",
  },
  "sim.show.departure": {
    verdict: "impossible",
    reason: "同上 · 零票离场",
  },
  "sim.show.episode_gate": {
    verdict: "impossible",
    reason: "同上 · 期节点门",
  },
  "sim.show.heart_pick": {
    verdict: "impossible",
    reason: "同上 · 心动投票",
  },
  "sim.show.pair_formed": {
    verdict: "impossible",
    reason: "同上 · 互选配对",
  },
  "sim.show.reveal": {
    verdict: "impossible",
    reason: "同上 · 公布环节",
  },
  "sim.show.zero_vote_alert": {
    verdict: "impossible",
    reason: "同上 · 零票预警",
  },
};

/** 锚 B · 桌面交互面（apps/desktop/.../components/chat 顶层 .tsx + `ask/` 子树）→ 手机对等裁决。
 *  key = 组件相对 chat 根的路径（正斜杠、去扩展名；顶层无前缀，ask 子树为 `ask/…`）。
 *  parity.check.ts 扫对照面断言每个 .tsx 都在此有键，并报告指向已不存在文件的陈旧键。
 *  infra / 渲染叶子记 `internal`（仍要求一句 reason，强制是有意分类而非遗漏）。 */
export const DESKTOP_CHAT_PARITY: Record<string, ParityEntry> = {
  // —— 互动卡：已上手机 ——
  NonBlockingAskCard: {
    verdict: "ported",
    surface: "NonBlockingAskCard（resolved）／HangingQuestionBar（pending）",
  },
  EscalationCard: {
    verdict: "ported",
    surface: "AssistantView · EscalationAnswer (②)",
  },
  BrowserLoginDecisionCard: {
    verdict: "ported",
    surface:
      "ResumeCard · ask_user browser_login 冷路 / EscalationAnswer · escalate browser_login 热路（BrowserLoginDecisionCard；「查看直播」→ ChatPage BrowserLiveSheet）",
  },
  MemoryUpdateCard: { verdict: "ported", surface: "MemoryUpdateCard (③)" },
  CheckpointCard: {
    verdict: "ported",
    surface: "ResumeCard",
    reason:
      "ask intent 专用面已对等进 ResumeCard（decision/kickoff compose+其他；proposal/risk 行选；organize/daily 勾选墙）；本机目录 action 手机禁用",
  },
  TeamPreviewCard: {
    verdict: "ported",
    surface:
      "ProcessTimeline · team_preview resolved 痕迹（已调整·已交回修订等）；pending 操作面在 ResumeCard",
    reason:
      "桌面 TeamPreviewCard 是 resolved 痕迹卡（pending 不占时间线，可操作面在 ResumePrompt）。手机原先只把 pending 开工卡标成 ported。现补时间线痕迹；pending 仍在 ResumeCard 三态机",
  },
  ApprovalPrompt: { verdict: "ported", surface: "PauseCard" },
  HotDecisionTrace: {
    verdict: "ported",
    surface: "AssistantView · hot-trace 轻状态行（resolved 门控，D3）",
  },
  SettledElsewhereNotices: {
    verdict: "ported",
    surface: "RemoteSettledCards ·「已由另一端处理」（B2 P1 · 验收 5）",
    reason:
      "同样只收「本端此刻正摆着」的卡（visibleCardIds 门控，排除重放）+ answeredByAPerson 同口径排除无人参与的收口；桌面 8s 自退，手机改「知道了」手动收——手机常在后台，自退会让用户回来只看到卡凭空消失",
  },
  ResumeSettledNotices: {
    verdict: "simplified",
    reason:
      "冷 resume 撞上已被消费的挂起帧（EPHEMERAL `resume_settled`）后的收口痕迹：桌面在原位留一条中性信息态只读条，说清这张卡何时以什么决策结的 + 本回合最终去向，8 秒自退（`turn_status=running` 不出条，紧接着就是续跑实时流；线材里没有处理方，故绝口不提「谁」）。手机沿用既有收口：ChatPage resume() 的流正常收尾后 markColdResolved 直接撤卡，不读 decision/decided_at/turn_status，也不留痕迹——差距在非 running 收尾时卡凭空消失，用户拿不到「这张卡早就结了、结果是什么」的交代。手机 RemoteSettledCards 不承接本条：那条是「另一端的人拍的」归属语义，借来会替用户认领一个没发生过的动作",
  },
  ResumePrompt: {
    verdict: "ported",
    surface: "ResumeCard",
    reason:
      "桌面 ResumePrompt 复用 CheckpointCard；手机 ResumeCard 已承接全 ask intent 专用面（本机目录 action 除外）",
  },
  HangingQuestionBar: {
    verdict: "ported",
    surface: "HangingQuestionBar",
    reason:
      "非阻塞悬题可操作面在底栏（有事等你，团队照跑）；与 ResumeCard「需要你拍板」一眼可分；pending 不进过程线",
  },
  PausedContinueSurface: {
    verdict: "ported",
    surface: "PausedContinueCard",
    reason:
      "CEO 限流暂停（outcome=paused）：已暂停 + 继续；闸卡暂停仍走 ResumeCard，不进本面",
  },
  FileArtifactsCard: { verdict: "ported", surface: "FileArtifactsCard" },
  TurnFileChangesReview: {
    verdict: "ported",
    surface:
      "TurnFileChangesReview（产物卡内展开；仅云 files/diff + restoreSnapshot，无 Local sidecar）",
  },
  StageCard: { verdict: "ported", surface: "StageCard" },
  StageCardDock: {
    verdict: "ported",
    surface: "StageCard mounted in ChatPage",
  },
  ConversationOutline: {
    verdict: "simplified",
    reason: "对话大纲/回合导航，手机暂不做（小屏以滚动代）",
  },
  FindBar: {
    verdict: "simplified",
    reason: "会话内查找，手机暂不做（无 Cmd+F 快捷键）",
  },
  ReceivedContext: {
    verdict: "ported",
    surface: "AssistantMessageFooter · 更多 → 收到的上下文（含 system）",
  },
  TeamNotesPanel: { verdict: "ported", surface: "TeamView · 团队便签" },
  InterjectionTimeline: {
    verdict: "ported",
    surface:
      "ProcessTimeline · user_interjection marker 槽 + InterjectionBubbles（五态不变；经典 steer 亦进泡；fold agentMentions → 点名芯片；旧 journal 无 marker 时 AssistantContent 尾部回退）",
  },
  QueuedTurnsBar: {
    verdict: "ported",
    surface:
      "ChatPage · QueuedTurnsBar 为唯一排队 UI（GET /queued-turns 权威对账；queuedTurns 多 FIFO；插话升格项标「来自你的插话」；按项取消；出队再进泡；重启丢队轻提示）",
  },
  SourceCards: { verdict: "ported", surface: "AssistantView · 来源" },
  CitationTierBadge: {
    verdict: "ported",
    surface: "AssistantView · 来源可信度徽标",
  },
  StatusStrip: {
    verdict: "simplified",
    surface: "TeamView · 团队条（图标 + n/m）",
    reason:
      "有意精简：无桌面图控件、打开辩论室；后台只挂徽标，协调等待只盖 n/m",
  },
  DebateProgressLine: {
    verdict: "simplified",
    surface: "TeamView · 辩论进展预览（fold 对齐 StatusStrip）",
    reason: "进展预览有；无桌面状态条「打开辩论室」进赛事页 CTA",
  },
  TurnWarningBanner: {
    verdict: "ported",
    surface: "ChatPage · 预检警告条",
  },
  AutoFolderNoticeCard: {
    verdict: "ported",
    surface: "AutoFolderNoticeCard · 独立卡 / FileArtifactsCard 卡头一行",
  },
  ParallelTimeline: {
    verdict: "ported",
    surface: "AssistantView · ProcessTimeline",
  },
  GraphAppendAnchor: {
    verdict: "simplified",
    reason:
      "桌面可滚回宿主图；手机仅「续自上一张图」文案行（旧 graph_append / 新 prev_execution_id）；新回合自带完整 TeamView，无跨气泡跳转",
  },

  // —— 提问 intent 专用卡（ask/；桌面 CheckpointCard 分支出；手机 ResumeCard 承接）——
  "ask/AskDecisionBody": {
    verdict: "ported",
    surface:
      "ResumeCard · decision/kickoff（default 预选 + compose 答复 +「其他」逃逸；本机目录 action → LocalPickerFailureCard unavailable）",
  },
  "ask/AskCommenceKickoff": {
    verdict: "internal",
    reason:
      "已退役 kickoff V2 Brief+Choose；仅离线预览对照，生产走 AskDecisionBody",
  },
  "ask/ProposalPickBody": {
    verdict: "ported",
    surface:
      "ResumeCard · proposal_pick 行式单选（未选禁 CTA「采用此方案」；提交带 selected）",
  },
  "ask/RiskAckBody": {
    verdict: "ported",
    surface:
      "ResumeCard · risk_ack 行式多选（parseRiskLabel 严重度灰字；空选可继续）",
  },
  "ask/OrganizePlanBody": {
    verdict: "ported",
    surface:
      "ResumeCard · organize_plan 勾选墙（默认全选 / 取消=剔除 / 空选禁 CTA「确认并整理（n）」）",
  },
  "ask/DailyReviewBody": {
    verdict: "ported",
    surface:
      "ResumeCard · daily_review 勾选墙（默认全选 / 取消=跳过 / selected 语义对齐）",
  },
  "ask/AskUserFields": {
    verdict: "ported",
    surface:
      "ResumeCard 内嵌 default 预选 +「其他」逃逸 + composeAnswer（答复模型 α）；无独立 AskUserFields 面",
  },
  "ask/LocalPickerFailureCard": {
    verdict: "ported",
    surface:
      "ResumeCard · 本机目录 action 点选 / Continue 拦截 → LocalPickerFailureCard（unavailable）",
  },
  "ask/AskCardShell": {
    verdict: "internal",
    reason:
      "ask intent 共用卡壳（头/体/底）；kickoff/decision 同壳，非独立对等面",
  },
  "ask/AskOptionRow": {
    verdict: "internal",
    reason: "ask 行式选项组（AskRowGroup），非独立对等面",
  },
  "ask/AskCommenceParts": {
    verdict: "internal",
    reason:
      "kickoff 预览/退役路径共享 chrome（OptionButton 等）；生产行式卡不再依赖",
  },
  "ask/preview/AskCommenceShared": {
    verdict: "internal",
    reason: "ask commence 离线预览共享叶（开发自检），非用户产品面",
  },
  "ask/preview/AskCommenceV1": {
    verdict: "internal",
    reason: "ask commence 离线预览变体（开发自检），非用户产品面",
  },
  "ask/preview/AskCommenceV2": {
    verdict: "internal",
    reason: "ask commence 离线预览变体（退役 V2 对照），非用户产品面",
  },
  "ask/preview/AskCommenceV3": {
    verdict: "internal",
    reason: "ask commence 离线预览变体（开发自检），非用户产品面",
  },
  "ask/preview/AskCommenceV4": {
    verdict: "internal",
    reason: "ask commence 离线预览变体（开发自检），非用户产品面",
  },
  "ask/preview/AskCommenceV5": {
    verdict: "internal",
    reason:
      "ask commence 离线预览：挂载生产 AskDecisionBody（通用澄清），非独立产品面",
  },

  // —— 有意精简 ——
  InlineTeamGraph: {
    verdict: "simplified",
    reason:
      "有意竖排 TeamView，非待做画布（不接 React Flow）；点队员卡下钻 RunDetail（对齐桌面抽屉，小屏合理）",
  },
  MentionMenu: {
    verdict: "ported",
    surface:
      "ComposerMentionSheet · ＋/@ 分类 sheet（附件/团队/对话/文件夹/文件）；ChatPage 历史用户气泡 + InterjectionBubbles 角色点名芯片",
    reason:
      "各端新建；附件走系统选文件，文件/文件夹只列云端索引；选中进草稿 attachments.kind=file/dir/conversation；团队点名走 agent_mentions（REST 历史用户气泡 / user_interjection SSE 插话气泡回放「点名」芯片，不暗示已派单，不混 kind）",
  },
  RetryBanner: {
    verdict: "ported",
    surface: "ChatPage · 错误条（去配置 / 重连）",
  },
  SourcePreview: {
    verdict: "simplified",
    reason: "手机来源为纯链接，无悬浮预览（桌面 affordance）",
  },
  ReadUrlSourceCollection: {
    verdict: "simplified",
    reason:
      "桌面把 ≥2 条连续 read_url 工具步合并为来源集合（SourceCards 式 favicon pill 行 / 展开来源列表）；手机 AssistantView 工具步逐条呈现 read_url（tool_use_end 已 ported），未做该桌面渲染层聚合",
  },
  BrowserActivityCard: {
    verdict: "simplified",
    reason:
      "L3 团队浏览器 M0：桌面把 worker browser_* 步聚合成关键帧活动卡（BrowserActivityCard / 单步 BrowserResult + 懒取工作区关键帧）；手机按 D12 退化为文本工具行（tool_use_end 已 ported）；直播入口在登录卡「查看直播」→ BrowserLiveSheet，富活动卡后置",
  },
  BrowserTakeoverCard: {
    verdict: "simplified",
    reason:
      "L3 团队浏览器 M2 接管留档卡（桌面直播面板发起接管后的只读时间线痕迹，起止 DURABLE 标记走 REST/store，接管期零帧落盘）；手机 BrowserLiveSheet 已支持直播/接管操作，留档卡后置",
  },
  PermissionChangeLine: {
    verdict: "simplified",
    reason:
      "桌面把会话级权限轴切换（PUT permission-axes → 审计 permission.axes_changed，走 REST 非事件通道）在对话流内渲染「权限 A → B」系统提示行；手机已可在 composer「＋」→ 本会话权限改四轴（对齐 PUT permission-axes），流内 A→B 系统行仍后置",
  },
  ConversationHydrateOverlay: {
    verdict: "simplified",
    reason:
      "桌面冷加载/hydrate 失败全屏诚实壳（防空草稿可发送）；手机 ChatPage 用自身 loading/error 态承接，不做同构全屏 overlay",
  },

  // —— 物理做不到 ——
  BackgroundTaskCard: {
    verdict: "impossible",
    reason: "本地↔云后台任务桥，手机无本地侧 (④)",
  },
  BackgroundTaskReview: {
    verdict: "impossible",
    reason: "同上 · 评审并把云端改动合并回本地磁盘",
  },
  DraftWorkspaceAssignPrompt: {
    verdict: "impossible",
    reason: "指派本地工作区，手机无本地",
  },
  RunConfirmPrompt: {
    verdict: "impossible",
    reason: "用户直触 bash 的本地运行确认卡（fsApi 本会话放行），手机无本地侧",
  },
  DelegationAuthorizationCard: {
    verdict: "ported",
    surface: "DelegationAuthorizationCard",
  },

  // —— infra / 渲染叶子（非交互-对等面）——
  ChatView: { verdict: "internal", reason: "对话容器" },
  ConversationDecisionPrompts: {
    verdict: "internal",
    reason:
      "决策卡单挂载容器（提问确认统一重构 P2：Chat/画布互斥复用同一实例），本身无 UI",
  },
  ConversationRoute: { verdict: "internal", reason: "路由壳" },
  MessageList: { verdict: "internal", reason: "消息列表容器" },
  MessageBubble: { verdict: "internal", reason: "气泡容器" },
  MessageInput: { verdict: "internal", reason: "composer 输入" },
  ToolLine: {
    verdict: "internal",
    reason: "工具行渲染叶（手机自有 ToolStep）",
  },
  Markdown: { verdict: "internal", reason: "共享渲染叶" },
  CodeBlock: { verdict: "internal", reason: "代码块渲染叶" },
  Diagram: { verdict: "internal", reason: "mermaid 渲染叶" },
  Favicon: { verdict: "internal", reason: "站点图标叶" },
  EvidenceBadge: {
    verdict: "ported",
    surface:
      "RunDetail · 输出（辩手发言全文）—— 手机独立 remarkEvidence 把【已核实·出处】/【待核实·推断】渲成 EvidenceBadge 徽章；`#eN` 台账解析 + 溯源底栏（含约定文档路径/幕1 #rN，可跳转对话文件页）与桌面同构（批 D2）",
  },
  EvidenceLedgerContext: {
    verdict: "ported",
    surface:
      "EvidenceLedgerContext（手机自有）—— 场级台账 map 注入徽章解析 `#eN`；溯源 Popover 桌面先行",
  },
  // 引用即出处 P1：回合调研台账通道 fold 进 ProjectedTurn.evidenceLedger / citedIds；
  // Citation.id 透传。来源卡 id 溯源完整面板桌面先行（对齐 O7）。
};

/** 锚 C · 桌面页面（apps/desktop/src/renderer/pages 下每个 .tsx，含子目录）→ 手机对等裁决。
 *  key = 相对 pages 根的路径（正斜杠、去 .tsx），子目录区分同名页（桶文件 ConversationsPage vs
 *  实体 conversations/ConversationsPage）。parity.check.ts 递归扫该目录断言每个 .tsx 都有键、并报
 *  陈旧键。接住整页 / route 级漂移：桌面新增一页 → conformance 失败直到给出手机对等裁决。 */
export const DESKTOP_PAGE_PARITY: Record<string, ParityEntry> = {
  // —— 已上手机（路由对齐）——
  ConversationPage: { verdict: "ported", surface: "ChatPage（对话）" },
  "conversations/ConversationsPage": {
    verdict: "ported",
    surface:
      "ChatPage · 会话列表（抽屉置顶三区；云组头进我的文件、＋ 在此新开；已归档 / 最近删除 / 分享）",
  },
  MorePage: { verdict: "ported", surface: "MorePage（设置中心）" },
  OnboardingPreviewPage: {
    verdict: "simplified",
    reason: "桌面 #/preview 离线 onboarding 场景页；手机无对等预览路由",
  },
  LoginPage: { verdict: "ported", surface: "LoginPage" },
  FilesPage: {
    verdict: "ported",
    surface:
      "FilesPage（会话别名寻址）/ WorkspacesPage「我的文件」（文件夹 / 对话产物 / 共享空间）→ WorkspaceFilesPage；云工作区可写。本机仍只读并过滤。工作区新建/删除仍归桌面；草稿可点选已有云文件夹",
  },
  MessagesPage: { verdict: "ported", surface: "MessagesPage（IM）+ im/*" },
  ServiceUnavailablePage: {
    verdict: "ported",
    surface: "ServiceUnavailablePage",
  },
  "more/UsageSettings": { verdict: "ported", surface: "more/UsageSettings" },
  "more/ModelSettings": { verdict: "ported", surface: "more/ModelSettings" },
  "more/ProfileModelSelect": {
    verdict: "internal",
    reason: "ModelSettings 内模型选择子控件，非独立页面",
  },
  "more/MoreIndexRedirect": {
    verdict: "simplified",
    reason: "桌面 /more 入口按 billing/服务商分流；手机 more 直达模型页",
  },
  "more/ProviderSettings": {
    verdict: "ported",
    surface: "more/ModelSettings · ProviderForm（手机合页）",
  },
  "more/AboutSettings": { verdict: "ported", surface: "more/AboutSettings" },
  "more/AccountSettings": {
    verdict: "ported",
    surface: "more/AccountSettings",
  },
  "more/ImPrivacySettings": {
    verdict: "ported",
    surface: "MessagesPage · 消息隐私（IM 设置）",
  },
  "more/FeedbackSettings": {
    verdict: "simplified",
    reason: "反馈设置页，手机暂不做",
  },
  "more/GitCredentialSettings": {
    verdict: "simplified",
    reason:
      "GitHub PAT / clone·SCM 凭据页仅桌面本波；手机无本地 SCM 与 clone 对话框，云 clone 凭据后置",
  },
  "more/RedirectToOfficialChat": {
    verdict: "simplified",
    reason:
      "桌面旧 #/more/notices 跳转 IM 官方号；手机公告走消息页官方会话（无独立公告设置页）",
  },
  "more/LoginSessionsSection": {
    verdict: "ported",
    surface: "more/AccountSettings · 登录会话（sessionDisplay 同源裁决）",
  },

  // —— 有意精简 / 保持不做（⑥ 精简陪伴定位 & 明确决策）——
  ToolboxPage: { verdict: "simplified", reason: "工具箱保持不做（⑥）" },
  "toolbox/automations/AutomationsPage": {
    verdict: "simplified",
    reason:
      "站立任务 / 自动化收件箱仅桌面（云工作区定时·Webhook）；手机本波有意不接",
  },
  "toolbox/automations/InboxPanel": {
    verdict: "simplified",
    reason: "同上 · 站立任务收件箱仅桌面",
  },
  "toolbox/automations/StandingTaskEditor": {
    verdict: "simplified",
    reason: "同上 · 站立任务编辑仅桌面",
  },
  "toolbox/automations/StandingTasksPanel": {
    verdict: "simplified",
    reason: "同上 · 站立任务列表仅桌面",
  },
  "toolbox/workflows/WorkflowsPage": {
    verdict: "simplified",
    reason: "用户工作流画布/CRUD/直跑仅桌面；手机本波有意不接",
  },
  "toolbox/workflows/WorkflowEditorPage": {
    verdict: "simplified",
    reason: "同上 · 工作流编辑仅桌面",
  },
  "toolbox/workflows/WorkflowCanvas": {
    verdict: "simplified",
    reason: "同上 · 工作流画布仅桌面",
  },
  "toolbox/workflows/WorkflowNodeInspector": {
    verdict: "simplified",
    reason: "同上 · 节点检查器仅桌面",
  },
  "toolbox/workflows/WorkflowSlotsPanel": {
    verdict: "simplified",
    reason: "同上 · 可换参数（槽位）面板仅桌面",
  },
  "toolbox/workflows/workflowNodes": {
    verdict: "simplified",
    reason: "同上 · 画布节点叶仅桌面",
  },
  "toolbox/workflows/UseTemplateDialog": {
    verdict: "simplified",
    reason: "同上 · 从官方模板复制仅桌面",
  },
  "toolbox/workflows/OfficialTemplateGuide": {
    verdict: "simplified",
    reason: "同上 · 官方模板说明仅桌面",
  },
  "toolbox/workflows/RunWorkflowDialog": {
    verdict: "simplified",
    reason: "同上 · 工作流直跑对话框仅桌面",
  },
  "toolbox/ConnectorsPage": {
    verdict: "impossible",
    reason: "本机 MCP stdio 连接器仅 Electron；手机无本机 MCP Client",
  },
  "toolbox/ToolsPage": {
    verdict: "simplified",
    reason: "工具创作保持不做（⑥）",
  },
  "toolbox/GuidelinesPage": {
    verdict: "simplified",
    reason: "工具箱·指南保持不做（⑥）",
  },
  "toolbox/manual/ManualShell": {
    verdict: "simplified",
    reason: "产品手册归工具箱保持不做（本轮决策）",
  },
  "toolbox/manual/ManualIntro": {
    verdict: "simplified",
    reason: "产品手册保持不做（本轮决策）",
  },
  "toolbox/manual/ManualMechanism": {
    verdict: "simplified",
    reason: "产品手册保持不做（本轮决策）",
  },
  "toolbox/manual/ManualCollaboration": {
    verdict: "simplified",
    reason: "产品手册保持不做（本轮决策）",
  },
  "toolbox/manual/ManualReference": {
    verdict: "simplified",
    reason: "产品手册保持不做（本轮决策）",
  },
  "toolbox/manual/embeds/ManualApprovalCardPreview": {
    verdict: "simplified",
    reason: "产品手册内嵌预览，手册保持不做（本轮决策）",
  },
  "toolbox/manual/embeds/ManualCheckpointCardPreview": {
    verdict: "simplified",
    reason: "产品手册内嵌预览，手册保持不做（本轮决策）",
  },
  "toolbox/manual/embeds/ManualDebateFinalePreview": {
    verdict: "simplified",
    reason: "产品手册内嵌预览，手册保持不做（本轮决策）",
  },
  "toolbox/manual/embeds/ManualDebateScoreboardPreview": {
    verdict: "simplified",
    reason: "产品手册内嵌预览，手册保持不做（本轮决策）",
  },
  "more/GeneralSettings": {
    verdict: "simplified",
    reason:
      "桌面「外观」合页为「通用」；手机不提供暗色切换，进阶开关亦无对应面（无诊断面板、无本机执行引擎）",
  },

  // —— 物理做不到（绑桌面画布 / 硬件）——
  FloatWindowPage: {
    verdict: "impossible",
    reason: "真 OS 浮窗路由（#/float）仅 Electron 多窗；手机无独立 OS 窗",
  },
  WhiteboardPage: {
    verdict: "impossible",
    reason: "协作白板入口，手机无板（与 board_* 事件同裁）",
  },
  WhiteboardCanvasPage: {
    verdict: "impossible",
    reason: "协作白板画布，手机无板",
  },
  WhiteboardPreviewPage: {
    verdict: "internal",
    reason:
      "桌面白板离线自检回放（#/preview/whiteboard 开发工具），非用户产品面",
  },
  "simulation/TownLauncherPage": {
    verdict: "impossible",
    reason: "AI 小镇 AgentTown 独立客户端启动页，桌面专属，手机无模拟面",
  },
  "more/ShortcutsSettings": {
    verdict: "impossible",
    reason: "手机无物理键盘，快捷键设置无意义",
  },

  // —— infra / 渲染叶 / 桶文件 / 开发自检（非用户-对等面）——
  ConversationsPage: {
    verdict: "internal",
    reason: "桶文件 re-export ./conversations/ConversationsPage",
  },
  "more/SettingsHeader": {
    verdict: "internal",
    reason: "设置页共享头部渲染叶（非独立面）",
  },
  "toolbox/manual/primitives": {
    verdict: "internal",
    reason: "产品手册渲染基件（非独立面）",
  },
  "toolbox/manual/BlockRenderer": {
    verdict: "internal",
    reason: "产品手册渲染基件（非独立面）",
  },
  "toolbox/manual/ChapterRenderer": {
    verdict: "internal",
    reason: "产品手册渲染基件（非独立面）",
  },
  "toolbox/manual/renderRichText": {
    verdict: "internal",
    reason: "产品手册富文本渲染基件（非独立面）",
  },
  PreviewPage: {
    verdict: "internal",
    reason: "桌面渲染层离线自检回放（#/preview 开发工具），非用户产品面",
  },
  CapabilityPacksPreviewPage: {
    verdict: "internal",
    reason: "桌面能力包离线自检预览（#/preview 开发工具），非用户产品面",
  },
  AskCommencePreviewPage: {
    verdict: "internal",
    reason:
      "桌面已退役 ask 开场布局对照（#/preview/ask-commence）；现生产 = AskDecisionBody，非用户产品面",
  },
  ConversationsPreviewPage: {
    verdict: "internal",
    reason:
      "桌面会话管理页离线预览（#/preview/conversations 开发自检），非用户产品面",
  },
  FilesPreviewPage: {
    verdict: "internal",
    reason: "桌面文件条目轨离线预览（#/preview/files 开发自检），非用户产品面",
  },
  "conversations/ConversationManageRow": {
    verdict: "internal",
    reason: "会话管理列表行渲染叶（ConversationsPage 拆件，非独立面）",
  },
  "conversations/ArchivedConversationManageRow": {
    verdict: "internal",
    reason: "已归档会话列表行渲染叶（ConversationsPage 拆件，非独立面）",
  },
  "conversations/DeletedFolderManageRow": {
    verdict: "internal",
    reason:
      "「最近删除」文件夹列表行渲染叶（ConversationsPage 拆件，非独立面）",
  },
  "conversations/DeletedConversationManageRow": {
    verdict: "ported",
    surface:
      "ConversationDrawer · 最近删除（对话行恢复；无文件夹那半、无彻底删除）",
  },
  "conversations/CollaborationTimeline": {
    verdict: "simplified",
    reason:
      "桌面项目协作时间线+阶段产物；手机有意不挂文件页——文件页虽已从只读升级为可写（浏览/预览/上传 + 改名/移动/删除/新建文件夹/CAS 编辑/软删区还原），承载的仍只是「这个工作区里的文件」，项目级协作聚合另说，暂无独立入口",
  },
  TurnDetailPage: {
    verdict: "simplified",
    reason: "桌面 run 详情全页；手机在 TeamView / 气泡内嵌简版",
  },

  // —— 法律文案（登录前 / 关于页入口）——
  "legal/LegalDocBody": {
    verdict: "ported",
    surface: "legal/LegalDocBody",
  },
  "legal/LegalDocPane": {
    verdict: "ported",
    surface: "legal/LegalDocPage（登录前全页阅读）",
  },
  "legal/LegalSettingsPage": {
    verdict: "ported",
    surface: "more/AboutSettings → /legal/:docId（LegalDocPage）",
  },
};
