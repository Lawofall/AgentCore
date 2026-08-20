/**
 * Mobile turn-stop lifecycle helpers（诚实过渡语义，各端全新建）。
 *
 * 对齐桌面契约语义，不共享实现：
 * - 点停止 → 可见「停止中」；UI 不先于后端进终态
 * - /stop 失败 → 诚实失败提示（可再点停止）
 * - 终态文案走「已停止」体系
 */

export type StopUiPhase = "idle" | "stopping";

export const STOPPING_LABEL = "停止中…";
export const STOP_BUTTON_LABEL = "停止";
export const STOP_FAILED_MESSAGE = "停止请求失败，引擎可能仍在运行";
/** 用户主动停止后的终态文案（团队 summarize / TeamView / 状态条；聊天气泡不占位）。 */
export const STOPPED_LABEL = "已停止";

/**
 * stopping：诚实过渡——继续消费 run_*（级联终态帧），丢弃正文/工具突变；
 * 仅后端 message_end / error 与无害 meta 定格。
 */
export function allowsEventWhileStopping(eventType: string): boolean {
  if (eventType.startsWith("run_")) return true;
  return (
    eventType === "message_end" ||
    eventType === "error" ||
    eventType === "turn_saved" ||
    eventType === "title_generated" ||
    eventType === "workspace_snapshot_done" ||
    eventType === "workspace_snapshot_failed" ||
    eventType === "citations" ||
    eventType === "evidence_ledger" ||
    eventType === "execution_detached" ||
    eventType === "execution_completed" ||
    // Stop 收口前后插话 / 排队帧须入折（勿永久卡在 received）。
    eventType === "user_interjection" ||
    eventType === "turn_queued"
  );
}

/** 后端终态帧：可解除 stopping（不伪造本地 cancelled）。 */
export function isStopConfirmEvent(eventType: string): boolean {
  return eventType === "message_end" || eventType === "error";
}

export function stopButtonLabel(phase: StopUiPhase): string {
  return phase === "stopping" ? STOPPING_LABEL : STOP_BUTTON_LABEL;
}

/** Composer / 输入禁用：流仍开着，或停止中等待确认。 */
export function isStopBusy(sending: boolean, phase: StopUiPhase): boolean {
  return sending || phase === "stopping";
}

export type StopPhaseAction =
  | "request_stop"
  | "stop_http_ok"
  | "stop_http_fail"
  | "confirm_terminal";

/**
 * Pure phase reducer — HTTP 失败回滚到 idle（继续看流）；不伪造终态。
 */
export function reduceStopPhase(
  phase: StopUiPhase,
  action: StopPhaseAction,
): StopUiPhase {
  switch (action) {
    case "request_stop":
      return "stopping";
    case "stop_http_fail":
      return "idle";
    case "confirm_terminal":
      return "idle";
    case "stop_http_ok":
      return phase === "stopping" ? "stopping" : phase;
    default:
      return phase;
  }
}
