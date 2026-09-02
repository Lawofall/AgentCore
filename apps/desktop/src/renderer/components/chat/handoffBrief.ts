import type { ProcessStep, RunDebrief } from "@/types/events";

/** Protocol ack written into the tool result. Never a user-facing peek or expand body. */
export const HANDOFF_RECEIPT = "已收尾。";

/**
 * Successful `handoff` tool row ≡ 交接简报卡 (`HandoffBriefCard`, same chrome
 * as the run-detail footer). Same predicate for ToolLine and RunDetailBody
 * (whether the footer card still renders). Failed / in-flight rows stay
 * ordinary tool lines.
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

/**
 * Live SSE carries `degraded` as an extra dict key on the debrief object
 * (`synthesize_debrief`). It is not on the `RunDebrief` wire type — read it
 * here, do not widen the contract.
 */
export function isDegradedDebrief(debrief: RunDebrief): boolean {
  return (
    "degraded" in debrief &&
    (debrief as { degraded?: unknown }).degraded === true
  );
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

/** Structured brief lives on the call arguments (legacy) or folded content. */
export function debriefFromHandoffArgs(
  args: Record<string, unknown>,
): RunDebrief {
  const degraded = args.degraded === true ? { degraded: true as const } : {};
  return {
    summary: asTrimmedString(args.summary) ?? null,
    key_points: asKeyPoints(args.key_points) ?? null,
    assumptions: asTrimmedString(args.assumptions) ?? null,
    next_steps: asTrimmedString(args.next_steps) ?? null,
    ...degraded,
  };
}

export function hasDebriefDetails(debrief: RunDebrief): boolean {
  return Boolean(
    (debrief.key_points && debrief.key_points.length > 0) ||
      debrief.assumptions ||
      debrief.next_steps,
  );
}

export function handoffSummaryPeek(args: Record<string, unknown>): string {
  return asTrimmedString(args.summary) ?? "";
}

function mergeFallback(
  arguments_: Record<string, unknown>,
  fallback: RunDebrief,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...arguments_ };
  const summary = fallback.summary?.trim();
  if (summary) merged.summary = summary;
  if (fallback.key_points?.length) merged.key_points = fallback.key_points;
  if (fallback.assumptions) merged.assumptions = fallback.assumptions;
  if (fallback.next_steps) merged.next_steps = fallback.next_steps;
  if (isDegradedDebrief(fallback)) merged.degraded = true;
  return merged;
}

/**
 * New rounds write the 便条 as the content step immediately before a successful
 * empty-arg `handoff`. Fold that prose onto the tool row so the card shows it
 * and the timeline does not paint the same block twice. Legacy rows that already
 * carry argument fields are left alone. `fallback` is `run.debrief` when the
 * process lost the content step (harvest still has the note).
 */
export function absorbHandoffBriefContent(
  process: readonly ProcessStep[],
  fallback?: RunDebrief | null,
): ProcessStep[] {
  const out: ProcessStep[] = [];
  for (const step of process) {
    if (!isSuccessfulHandoffStep(step)) {
      out.push(step);
      continue;
    }
    const fromArgs = debriefFromHandoffArgs(step.arguments);
    if (fromArgs.summary || hasDebriefDetails(fromArgs)) {
      out.push(step);
      continue;
    }
    const chunks: string[] = [];
    while (out.length > 0) {
      const last = out[out.length - 1];
      if (last.kind !== "content") break;
      const prev = out.pop();
      if (prev == null || prev.kind !== "content") break;
      chunks.unshift(prev.text);
    }
    const folded = chunks.join("").trim();
    let arguments_ = { ...step.arguments };
    if (folded) {
      arguments_ = { ...arguments_, summary: folded };
    } else if (
      fallback &&
      (fallback.summary?.trim() || hasDebriefDetails(fallback))
    ) {
      arguments_ = mergeFallback(arguments_, fallback);
    }
    out.push({ ...step, arguments: arguments_ });
  }
  return out;
}
