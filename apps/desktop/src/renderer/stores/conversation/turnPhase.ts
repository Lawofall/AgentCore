/**
 * 回合停止生命周期（键 = conversationId，与 abort 注册同槽）。
 *
 * idle → preflight → streaming → stopping → stopped|completed|failed
 *
 * AbortSignal 只负责物理断流；是否允许开流 / 是否接受内容事件以本 phase 为准。
 *
 * 本文件保持**纯函数**（无 store 依赖），避免与 `store.ts` 循环引用。
 * 读写 phase 的命令式 API 见 `turnPhaseActions.ts`。
 */

import { INTERACTION_ORPHANED_EVENT } from "@/types/interactionExt";
import { INTERACTION_KIND_WIRE } from "@agentcore/contract-types";

export type TurnPhase =
  | "idle"
  | "preflight"
  | "streaming"
  | "stopping"
  | "stopped"
  | "completed"
  | "failed";

export type TurnTerminalOutcome = "stopped" | "completed" | "failed";

/**
 * User-facing interaction `*_required` events from {@link INTERACTION_KIND_WIRE}.
 * Cold pause cards (ask_user / plan_review / team_preview) may arrive on the
 * same connection after `message_end` has already moved turnPhase to terminal;
 * dropping them leaves live UI without ResumePrompt until hard refresh.
 * Hot `*_required` (approval / escalation / …) share the same wire shape and
 * are allowlisted here too — still not a terminal free-for-all.
 */
const INTERACTION_REQUIRED_EVENTS: ReadonlySet<string> = new Set(
  Object.values(INTERACTION_KIND_WIRE)
    .map((w) => w.requiredEvent)
    .filter((name) => name.endsWith("_required")),
);

/**
 * 配对的 `*_resolved` 收口帧。`*_required` 既然能在这个窗把卡画出来（见上），它的收口
 * 帧就只可能在同一个窗到。
 */
const INTERACTION_RESOLVED_EVENTS: ReadonlySet<string> = new Set(
  Object.values(INTERACTION_KIND_WIRE)
    .map((w) => w.resolvedEvent)
    .filter((name): name is string => !!name?.endsWith("_resolved")),
);

export function isTerminalPhase(phase: TurnPhase): boolean {
  return phase === "stopped" || phase === "completed" || phase === "failed";
}

/** stopping / terminal：禁止新开流（探活恢复点、sidecar invoke、云 fetch）。 */
export function blocksStreamOpen(phase: TurnPhase): boolean {
  return phase === "stopping" || isTerminalPhase(phase);
}

/** 仅 streaming 允许重建气泡、追加正文/工具等流式突变。 */
export function allowsStreamingMutations(phase: TurnPhase): boolean {
  return phase === "streaming";
}

/** Worker-scoped tool_use_* — graph/strip chrome only, not captain timeline. */
function isWorkerScopedToolUse(eventType: string, payload: unknown): boolean {
  if (
    eventType !== "tool_use_start" &&
    eventType !== "tool_use_end" &&
    eventType !== "tool_use_progress"
  ) {
    return false;
  }
  if (!payload || typeof payload !== "object") return false;
  const runId = (payload as { run_id?: unknown }).run_id;
  return typeof runId === "string" && runId.length > 0;
}

/**
 * stopping：诚实过渡态——继续消费 run_*（含级联终态帧），正文/工具突变仍挡；
 * 仅后端 message_end/error 才定格。terminal：放行下一回合 message_start + 无害 meta。
 *
 * terminal 也放行 run_*：对齐云端 / sidecar D1——`message_end` 后 sink 仍可为 live
 * detached drive 续推 `run_completed` / `run_tool_progress`（conformance
 * `async_delivery`：detached → message_end → run_completed → execution_completed）。
 * 另放行**带 `run_id` 的** `tool_use_start` / `tool_use_end` / `tool_use_progress`：
 * 队员工具不进船长气泡（`appendToolStep` 已按 `run_id` 跳过），只驱动协作图 /
 * 状态条活体；挡掉则 detached 后最长执行窗零相位反馈。CEO 自身工具（无 `run_id`）
 * 仍挡——那是收口后的内容突变。
 * 若挡掉 run_* / 队员 tool_use_* / `team_synthesis_preview`，协作图会冻在收口前
 * 快照（队长节点团队进展预览同窗），直到（若有）execution_completed 刷新。
 *
 * stopping + terminal 另放行 INTERACTION_KIND_WIRE 的 `*_required`（见上常量）：
 * 冷挂起 ask 常紧挨 `message_end(paused)`，门闩若挡掉则 live 看不到拍板卡。
 *
 * 配对的 `*_resolved` 同窗放行。本端自己拍板不依赖这帧（提交路已乐观 `markResolved`），
 * 所以挡掉它伤的全是**本端没答**的那些收口：另一端拍板（多端同权「已由另一端处理」收口条
 * 的唯一来源——journal 水合被明确排除在外）、CEO 仲裁、按假设推进 / 墙钟超时（压根没有人
 * 答，连回执都没有）。挡掉等于让一张服务端早已结掉的卡继续显示可点。它只把卡推向
 * resolved（`markResolved` 建不出 pending），副作用也只有清挂起帧 / 记结算帧，不是内容突变。
 *
 * `interaction_orphaned` 同样放行：它**只出自收尾**（settlement 预写 / 服务重启对账），
 * 天然落在 `message_end` 之后的 terminal 窗。挡掉它就等于把「这张卡已经没人能收答复」
 * 这条事实丢在门口——卡继续显示可点，点必失败，直到刷新或切会话才变灰。它只把卡推向
 * 终态，不能复活任何 pending，所以在 terminal 放行不构成内容突变。
 *
 * Cloud CLIENT_TOOL `workspace_op_required` / `host_op_required` / … **不再**走
 * 会话 SSE：设备级履约通道跨会话，不绑 turnPhase；取消靠 `client_tool_cancelled`
 * abort 在飞 op。Sidecar 本地回合的同类帧在 `dispatchSSEEvent` 里于门闩之前履约
 *（`origin: "sidecar"`），故此处继续挡它们也不会误伤 sidecar。
 */
export function allowsSseEvent(
  phase: TurnPhase,
  eventType: string,
  payload?: unknown,
): boolean {
  if (phase === "idle" || phase === "preflight" || phase === "streaming") {
    return true;
  }
  // terminal：放行下一回合 message_start（跨回合 preview 回放 / 同连接连续回合）。
  if (eventType === "message_start" && isTerminalPhase(phase)) {
    return true;
  }
  // stopping + terminal：run_* 必须入折（停止级联 / 异步团队后台帧）；
  // 队员 tool_use_* 同窗放行，驱动 detached 后的图节点 / 状态条。
  if (
    (phase === "stopping" || isTerminalPhase(phase)) &&
    (eventType.startsWith("run_") || isWorkerScopedToolUse(eventType, payload))
  ) {
    return true;
  }
  // stopping + terminal：冷/热交互 required 帧（至少 checkpoint_required）与配对收口帧。
  if (
    (phase === "stopping" || isTerminalPhase(phase)) &&
    (INTERACTION_REQUIRED_EVENTS.has(eventType) ||
      INTERACTION_RESOLVED_EVENTS.has(eventType))
  ) {
    return true;
  }
  // stopping + terminal：收尾 orphan（见上；失效卡必须当场变灰）。
  if (
    (phase === "stopping" || isTerminalPhase(phase)) &&
    eventType === INTERACTION_ORPHANED_EVENT
  ) {
    return true;
  }
  return (
    eventType === "message_end" ||
    eventType === "error" ||
    eventType === "turn_saved" ||
    eventType === "title_generated" ||
    eventType === "citations" ||
    eventType === "evidence_ledger" ||
    // Post-turn auto-backup (after message_end): toast / clear failure banner.
    eventType === "workspace_snapshot_done" ||
    eventType === "workspace_snapshot_failed" ||
    // 排队按项取消：Stop 过程中仍可清 UI（Stop ≠ 取消排队，但 cancel 事件须入折）。
    eventType === "turn_queue_cancelled" ||
    // FIFO 出队开跑：常紧挨上一回合 terminal 之后、message_start 之前到达。
    eventType === "turn_queue_started" ||
    // Stop 后降级排队：user_interjection(queued) + turn_queued(degraded_from=steer)
    // 常落在 stopping/terminal；挡掉则气泡永久卡在 received。
    eventType === "user_interjection" ||
    eventType === "turn_queued" ||
    // 冷 resume deferred：wrap_up 可能落在宿主 message_end 之后的 terminal 窗。
    eventType === "resume_deferred" ||
    // 冷 resume 幂等成功：它是这条连接的首帧，而宿主回合的 message_end 可能刚把
    // phase 推进 terminal。挡掉等于把卡永远钉在「提交中」。
    eventType === "resume_settled" ||
    // 异步团队：detached 可落在 message_end 前后；completed 常在 terminal 后同连接到达。
    // `team_synthesis_preview` 同窗续推（队长节点团队进展，非气泡正文）。
    eventType === "execution_detached" ||
    eventType === "execution_completed" ||
    eventType === "team_synthesis_preview"
  );
}
