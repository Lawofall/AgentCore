import type { ExecutionStatus, RunStatus, WorkerRunPhase } from "./types";

/**
 * Single source for run / execution lifecycle copy (停止与中断呈现).
 * `cancelled` is always「已停止」— graph face, board overlay, and detail badges
 * must not drift to「已取消」.
 */
export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  pending: "等待",
  running: "进行中",
  completed: "已封口",
  failed: "失败",
  cancelled: "已停止",
  skipped: "未执行",
  cut: "已切出",
};

export function runStatusLabel(status: RunStatus): string {
  return RUN_STATUS_LABEL[status] ?? status;
}

/**
 * Worker mid-flight activity phase (`run_phase`) → face / badge copy.
 * Orthogonal to {@link RunStatus}: pending→排队中, skipped→未执行 stay on status.
 * Returns null when phase is absent (fall back to lifecycle label).
 */
export function runPhaseLabel(
  phase: WorkerRunPhase | null | undefined,
  phaseTool?: string | null,
  toolNameLabel?: (name: string) => string,
): string | null {
  if (!phase) return null;
  switch (phase) {
    case "thinking":
      return "思考中";
    case "tool":
      return phaseTool
        ? toolNameLabel
          ? toolNameLabel(phaseTool)
          : phaseTool
        : "调用工具";
    case "waiting_children":
      return "等待后面的段";
    case "winding_down":
      return "收尾中";
    default:
      return null;
  }
}

export function executionStatusLabel(status: ExecutionStatus): string {
  switch (status) {
    case "running":
      return "进行中";
    case "completed":
      return "已封口";
    case "failed":
      return "失败";
    case "cancelled":
      return "已停止";
    case "paused":
      return "已暂停";
    default:
      return "准备中";
  }
}
