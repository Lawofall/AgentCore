/**
 * 回合「用时」= 协作事实流的真实墙钟跨度（首条 → 末条协作事件）。
 *
 * 两端同名指标必须算同一个量。桌面一直取墙钟跨度（`elapsedMs(frames)`），手机曾取各队员
 * 时长之和 —— 同一回合桌面显示「用时 40s」、手机显示「用时 2m10s」，队员越多、并行度越高
 * 手机的数字越大，把并行省下的时间显示成了更慢。求和是工时不是用时；跨度才是用户等的那段。
 *
 * 事件集合镜像桌面 `frameFromEvent`（`apps/desktop/src/renderer/stores/execution/frames.ts`）
 * 能产帧的 wire 事件——正文 / 引用 / 心跳等非协作事件不计入跨度。桌面对识别不了的
 * `run_phase`（更新后端的新相位）不产帧，这里只按事件类型收；相位事件只出现在一个 run 开跑
 * 与终态之间，落不到首尾两端，因此跨度不受影响。
 */

/** 进入协作事实流的 wire 事件类型（跨度只由它们界定）。 */
export const RUN_FRAME_EVENT_TYPES: ReadonlySet<string> = new Set([
  "run_started",
  "run_context",
  "run_output_delta",
  "run_output_reset",
  "run_reasoning_delta",
  "run_tool_progress",
  "run_phase",
  "run_completed",
  "run_failed",
  "run_cancelled",
  "run_skipped",
  "run_progress",
  "batch_metrics",
  "run_escalation",
  "escalation_required",
  "escalation_resolved",
  "tool_use_start",
  "tool_use_end",
  "plan_review_required",
  "plan_review_resolved",
  "plan_revised",
]);

/** 该 wire 事件是否属于协作事实流（见 {@link RUN_FRAME_EVENT_TYPES}）。 */
export function isRunFrameEvent(type: string): boolean {
  return RUN_FRAME_EVENT_TYPES.has(type);
}

/** 结构化到只要 `{type, timestamp}`：两端各自的 SSEEvent 都满足，kit 不必依赖事件契约包。 */
export interface TimedWireEvent {
  type: string;
  timestamp?: string | null;
}

/**
 * 一个回合的协作墙钟跨度（毫秒）。不足两条带时戳的协作事件 → 0（没有可言的跨度，
 * 调用方据此不显示「用时」）。时戳解析不了的事件跳过，绝不用本机时钟顶替。
 */
export function turnElapsedMs(events: readonly TimedWireEvent[]): number {
  let first: number | null = null;
  let last = 0;
  for (const ev of events) {
    if (!isRunFrameEvent(ev.type)) continue;
    const t = ev.timestamp ? Date.parse(ev.timestamp) : Number.NaN;
    if (Number.isNaN(t)) continue;
    if (first === null) first = t;
    last = t;
  }
  if (first === null) return 0;
  return Math.max(0, last - first);
}
