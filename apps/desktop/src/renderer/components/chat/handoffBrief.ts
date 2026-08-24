import type { MotionCard, ProcessStep, RunDebrief } from "@/types/events";

/** Protocol ack written into the tool result. Never a user-facing peek or expand body. */
export const HANDOFF_RECEIPT = "已收尾并提交交接简报。";

/**
 * Successful `handoff` tool row ≡ 交接简报卡. Same predicate for ToolLine /
 * ToolResultView (row face) and RunDetailBody (whether the footer DebriefSection
 * still renders). Failed / in-flight rows stay ordinary tool lines.
 */
export function isSuccessfulHandoff(
  toolName: string,
  status: "running" | "success" | "error" | "redirect",
): boolean {
  return toolName === "handoff" && status === "success";
}

export function isSuccessfulHandoffStep(
  step: ProcessStep,
): step is Extract<ProcessStep, { kind: "tool" }> {
  return (
    step.kind === "tool" && isSuccessfulHandoff(step.tool_name, step.status)
  );
}

/** Timeline already has an authored brief — footer「交接简报」is only for degraded. */
export function processHasSuccessfulHandoff(
  process: readonly ProcessStep[],
): boolean {
  return process.some(isSuccessfulHandoffStep);
}

function asTrimmedString(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  const t = v.trim();
  return t || undefined;
}

function asKeyPoints(v: unknown): string[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const points = v
    .filter((p): p is string => typeof p === "string")
    .map((p) => p.trim())
    .filter(Boolean);
  return points.length > 0 ? points : undefined;
}

function asMotionCard(v: unknown): MotionCard | undefined {
  if (!v || typeof v !== "object") return undefined;
  const c = v as Partial<MotionCard>;
  if (typeof c.motion !== "string" || !c.motion.trim()) return undefined;
  if (!Array.isArray(c.sides) || typeof c.rationale !== "string") {
    return undefined;
  }
  if (!Array.isArray(c.fact_pointers) || typeof c.form !== "string") {
    return undefined;
  }
  return c as MotionCard;
}

/** Structured brief lives on the call arguments (not the protocol receipt). */
export function debriefFromHandoffArgs(
  args: Record<string, unknown>,
): RunDebrief {
  const motion_card = asMotionCard(args.motion_card);
  return {
    summary: asTrimmedString(args.summary) ?? null,
    key_points: asKeyPoints(args.key_points) ?? null,
    assumptions: asTrimmedString(args.assumptions) ?? null,
    next_steps: asTrimmedString(args.next_steps) ?? null,
    ...(motion_card ? { motion_card } : {}),
  };
}

export function hasDebriefDetails(debrief: RunDebrief): boolean {
  return Boolean(
    (debrief.key_points && debrief.key_points.length > 0) ||
      debrief.assumptions ||
      debrief.next_steps ||
      debrief.motion_card,
  );
}

export function handoffSummaryPeek(args: Record<string, unknown>): string {
  return asTrimmedString(args.summary) ?? "";
}
