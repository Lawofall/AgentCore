import type { RunDebrief } from "@agentcore/protocol-conformance";

/** Worker terminal tool — brief lives in the call's arguments, not the protocol ack. */
export const HANDOFF_TOOL_NAME = "handoff";

/**
 * 成功 handoff 行脸 ↔ 队员详情是否画页脚 DebriefBlock 的同一判定。
 * 成功 → 工具行即简报卡，页脚不再画；失败 / 未打到工具才把 debrief 留给页脚。
 */
export function isSuccessfulHandoff(toolName: string, status: string): boolean {
  return toolName === HANDOFF_TOOL_NAME && status === "success";
}

export function hasSuccessfulHandoff(
  calls: ReadonlyArray<{ toolName: string; status: string }>,
): boolean {
  return calls.some((c) => isSuccessfulHandoff(c.toolName, c.status));
}

/**
 * 从 handoff 工具参数抽出简报字段。不搬 `motion_card`——手机现无命题卡，勿借机补。
 */
export function debriefFromHandoffArgs(
  args: Record<string, unknown>,
): RunDebrief {
  const key_points = Array.isArray(args.key_points)
    ? args.key_points.filter(
        (p): p is string => typeof p === "string" && p.trim() !== "",
      )
    : undefined;
  return {
    summary: typeof args.summary === "string" ? args.summary : undefined,
    key_points: key_points && key_points.length > 0 ? key_points : undefined,
    assumptions:
      typeof args.assumptions === "string" ? args.assumptions : undefined,
    next_steps:
      typeof args.next_steps === "string" ? args.next_steps : undefined,
  };
}

export function hasDebriefDetails(debrief: RunDebrief): boolean {
  return Boolean(
    (debrief.key_points && debrief.key_points.length > 0) ||
      debrief.assumptions ||
      debrief.next_steps,
  );
}
