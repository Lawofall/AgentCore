import type { RunPlanPayload } from "@/types/events";
import type { ActKind, ExecutionAct, ExecutionPlan } from "./types";

/** Map wire / synthesized act declaration onto the plan skeleton. */
export function actFromRunPlan(p: RunPlanPayload): ExecutionAct {
  const raw = p.act;
  if (raw?.act_id) {
    const kind: ActKind =
      raw.kind === "debate" || raw.kind === "multi_agent"
        ? raw.kind
        : "multi_agent";
    const auth =
      raw.authorized_by === "stage_card" ||
      raw.authorized_by === "auto" ||
      raw.authorized_by === "preview"
        ? raw.authorized_by
        : null;
    return {
      actId: raw.act_id,
      kind,
      title: raw.title ?? null,
      anchorRunId: raw.anchor_run_id ?? null,
      authorizedBy: auth,
    };
  }
  // 兼容：旧 journal / 旧向量无 act → 合成单幕（act-1，kind = plan_type）。
  const kind: ActKind =
    p.plan_type === "debate" || p.plan_type === "multi_agent"
      ? p.plan_type
      : "multi_agent";
  return {
    actId: "act-1",
    kind,
    title: null,
    anchorRunId: null,
    authorizedBy: null,
  };
}

/** Map a `run_plan` wire payload to the immutable plan skeleton. */
export function planFromRunPlan(p: RunPlanPayload): ExecutionPlan {
  const act = actFromRunPlan(p);
  const prev =
    typeof p.prev_execution_id === "string" && p.prev_execution_id.trim()
      ? p.prev_execution_id.trim()
      : null;
  return {
    id: p.execution_id,
    planType: p.plan_type,
    taskSummary: p.task_summary,
    prevExecutionId: prev,
    acts: [act],
    noteWall: p.note_wall === true,
    agents: p.agents.map((a) => ({
      id: a.id,
      role: a.role,
      thinking: a.thinking,
    })),
    runs: p.runs.map((s) => ({
      id: s.id,
      agentId: s.agent_id,
      task: s.task,
      dependsOn: s.depends_on,
      parentRunId: s.parent_run_id ?? null,
      kind: s.kind,
      stance: s.stance,
      group: s.group,
      round: s.round,
      replacesRunId: s.replaces_run_id ?? null,
      actId: act.actId,
      // Presentation-only: first run_plan of a turn is 委派 #1. Not on the wire —
      // stamped here so merge / journal replay can tell later same-turn batches apart.
      delegateBatch: 1,
    })),
  };
}

/** Ensure every run has an explicit 1-based `delegateBatch` (default 1). */
export function ensureDelegateBatchStamps(plan: ExecutionPlan): ExecutionPlan {
  if (plan.runs.every((r) => r.delegateBatch != null)) return plan;
  return {
    ...plan,
    runs: plan.runs.map((r) => ({
      ...r,
      delegateBatch: r.delegateBatch ?? 1,
    })),
  };
}

/** Merge a later same-turn delegate batch into the current plan: append unseen
 * agents/runs (ids are namespaced per delegate call). Whole-plan `taskSummary`
 * stays the host/first act's title — later debate/append acts must not overwrite
 * the graph entry-node label. Shared by {@link ingestPlan} (live) and journal
 * replay (history).
 *
 * New runs get `delegateBatch = max(existing)+1` so the collaboration graph can
 * band「第 N 次委派」without inventing depends_on edges or changing the wire. */
export function mergePlanInto(
  cur: ExecutionPlan,
  next: ExecutionPlan,
): ExecutionPlan {
  const agents = [...cur.agents];
  for (const a of next.agents) {
    if (!agents.some((x) => x.id === a.id)) agents.push(a);
  }
  const acts = [...(cur.acts ?? [])];
  for (const a of next.acts ?? []) {
    const idx = acts.findIndex((x) => x.actId === a.actId);
    if (idx >= 0) acts[idx] = a;
    else acts.push(a);
  }
  const normalized = ensureDelegateBatchStamps(cur);
  let maxBatch = 0;
  for (const r of normalized.runs) {
    const b = r.delegateBatch ?? 1;
    if (b > maxBatch) maxBatch = b;
  }
  const nextBatch = maxBatch + 1;
  const runs = [...normalized.runs];
  for (const s of next.runs) {
    if (!runs.some((x) => x.id === s.id)) {
      runs.push({ ...s, delegateBatch: nextBatch });
    }
  }
  return {
    ...normalized,
    acts,
    agents,
    runs,
    // Prefer first/host summary so a later debate act cannot retitle the graph.
    taskSummary: normalized.taskSummary || next.taskSummary,
    // First plan wins — later same-id batches must not overwrite the续自 link.
    prevExecutionId: normalized.prevExecutionId ?? next.prevExecutionId ?? null,
    // 墙一旦升过，同回合后批不降（第二批可能省略字段）。
    noteWall: Boolean(normalized.noteWall || next.noteWall),
  };
}
