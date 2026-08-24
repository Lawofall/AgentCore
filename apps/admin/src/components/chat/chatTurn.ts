import type { ReplayMessage } from "@/services/adminObservability";
import type { ProcessStep } from "@agentcore/protocol-conformance";

/**
 * Replay assistant-row final state: content + runs_payload + nullable projected.
 * Same pair the user client reads on reload — no client fold, no journal
 * reverse-engineering, no synthesized message_end.
 *
 * `projected` is null for plain chat and process-only single-agent; process /
 * finish_reason then live on `runsPayload`. Production `projected` is a loose
 * dict — treat every nested field as optional.
 */
export type ChatRunsPayload = {
  process?: unknown[] | null;
  finish_reason?: string | null;
  error?: { code?: string; message?: string } | null;
  turn_warning?: string | null;
};

export type ChatTurnInput = {
  content?: string | null;
  runsPayload?: ChatRunsPayload | null;
  projected?: unknown;
  /** List-row thinking column (`ReplayMessage.reasoning_content`). */
  reasoningContent?: string | null;
};

export type NormalizedCitation = {
  url: string;
  title: string;
  snippet: string;
  site: string;
  id: string;
  tier: string;
};

export type NormalizedRun = {
  id: string;
  agentId: string;
  role: string;
  status: string;
  task: string;
  kind: string;
  parentRunId: string | null;
  outputSummary: string;
  error: string;
  debriefSummary: string;
  /** Per-worker timeline (symmetric with CEO `process`). */
  process: ProcessStep[];
};

export type NormalizedInteraction = {
  kind: string;
  id: string;
  status: string;
  /** `ask_user` leaf already carries this; other kinds stay "". */
  question: string;
};

export type NormalizedProjected = {
  status: string | null;
  finishReason: string | null;
  outcome: string | null;
  content: string;
  reasoning: string;
  process: ProcessStep[];
  citations: NormalizedCitation[];
  runs: NormalizedRun[];
  progress: { completed: number; total: number };
  interactions: NormalizedInteraction[];
  debate: { form: string; motion: string } | null;
  deliveryStatus: { state: string } | null;
  error: { code: string; message: string } | null;
  turnWarning: string | null;
};

export type ResolvedChatTurn = {
  content: string;
  process: ProcessStep[];
  reasoning: string;
  projected: NormalizedProjected | null;
  finishReason: string | null;
  status: string | null;
  outcome: string | null;
  error: { code?: string; message?: string } | null;
  turnWarning: string | null;
  citations: NormalizedCitation[];
  runs: NormalizedRun[];
  progress: { completed: number; total: number };
  interactions: NormalizedInteraction[];
  debate: { form: string; motion: string } | null;
  deliveryStatus: { state: string } | null;
};

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function asString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

export function asProjectedTurn(raw: unknown): Record<string, unknown> | null {
  return asRecord(raw);
}

export function asProcessSteps(raw: unknown): ProcessStep[] | null {
  if (!Array.isArray(raw)) return null;
  return raw.filter(
    (s): s is ProcessStep =>
      Boolean(s) && typeof s === "object" && "kind" in s,
  );
}

function normalizeCitation(raw: unknown, index: number): NormalizedCitation {
  const o = asRecord(raw) ?? {};
  return {
    url: asString(o.url) ?? "",
    title: asString(o.title) ?? "",
    snippet: asString(o.snippet) ?? "",
    site: asString(o.site) ?? "",
    id: asString(o.id) ?? `#r${index + 1}`,
    tier: asString(o.tier) ?? "",
  };
}

function normalizeRun(raw: unknown): NormalizedRun | null {
  const o = asRecord(raw);
  if (!o) return null;
  const id = asString(o.id);
  if (!id) return null;
  const debrief = asRecord(o.debrief);
  return {
    id,
    agentId: asString(o.agentId) ?? "",
    role: asString(o.role) ?? "",
    status: asString(o.status) ?? "pending",
    task: asString(o.task) ?? "",
    kind: asString(o.kind) ?? "agent",
    parentRunId: asString(o.parentRunId),
    outputSummary: asString(o.outputSummary) ?? "",
    error: asString(o.error) ?? "",
    debriefSummary: asString(debrief?.summary) ?? "",
    process: asProcessSteps(o.process) ?? [],
  };
}

function normalizeInteraction(raw: unknown): NormalizedInteraction | null {
  const o = asRecord(raw);
  if (!o) return null;
  const kind = asString(o.kind);
  if (!kind) return null;
  return {
    kind,
    id: asString(o.id) ?? "",
    status: asString(o.status) ?? "",
    question: asString(o.question) ?? "",
  };
}

export function stepString(step: ProcessStep, ...keys: string[]): string {
  const rec = step as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = rec[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/** Resolved ask with a question, matched onto a checkpoint marker. Pending / empty → null. */
export function resolvedAskForStep(
  step: ProcessStep,
  interactions: NormalizedInteraction[],
): NormalizedInteraction | null {
  if (step.kind !== "checkpoint" && (step.kind as string) !== "ask") {
    return null;
  }
  const id = stepString(step, "checkpoint_id", "checkpointId", "id");
  if (!id) return null;
  const match =
    interactions.find(
      (item) =>
        item.id === id && (item.kind === "ask_user" || item.kind === "ask"),
    ) ?? interactions.find((item) => item.id === id);
  if (!match || match.status !== "resolved" || !match.question.trim()) {
    return null;
  }
  return match;
}

export function slottedResolvedAskIds(
  process: ProcessStep[],
  interactions: NormalizedInteraction[],
): Set<string> {
  const ids = new Set<string>();
  for (const step of process) {
    const ask = resolvedAskForStep(step, interactions);
    if (ask?.id) ids.add(ask.id);
  }
  return ids;
}

export function normalizeProjected(raw: unknown): NormalizedProjected | null {
  const o = asRecord(raw);
  if (!o) return null;
  const runs = Array.isArray(o.runs)
    ? o.runs.map(normalizeRun).filter((r): r is NormalizedRun => r !== null)
    : [];
  const progressRaw = asRecord(o.progress);
  const completed =
    typeof progressRaw?.completed === "number"
      ? progressRaw.completed
      : runs.filter((r) => r.status === "completed").length;
  const total =
    typeof progressRaw?.total === "number" ? progressRaw.total : runs.length;
  const debate = asRecord(o.debate);
  const delivery = asRecord(o.deliveryStatus);
  const err = asRecord(o.error);
  return {
    status: asString(o.status),
    finishReason: asString(o.finishReason),
    outcome: asString(o.outcome),
    content: asString(o.content) ?? "",
    reasoning: asString(o.reasoning) ?? "",
    process: asProcessSteps(o.process) ?? [],
    citations: Array.isArray(o.citations)
      ? o.citations.map(normalizeCitation)
      : [],
    runs,
    progress: { completed, total },
    interactions: Array.isArray(o.interactions)
      ? o.interactions
          .map(normalizeInteraction)
          .filter((i): i is NormalizedInteraction => i !== null)
      : [],
    debate: debate
      ? {
          form: asString(debate.form) ?? "",
          motion: asString(debate.motion) ?? "",
        }
      : null,
    deliveryStatus: delivery
      ? { state: asString(delivery.state) ?? "" }
      : null,
    error: err
      ? {
          code: asString(err.code) ?? "",
          message: asString(err.message) ?? "",
        }
      : null,
    turnWarning: asString(o.turnWarning),
  };
}

export function chatTurnFromReplay(
  message: Pick<
    ReplayMessage,
    "content" | "runs_payload" | "projected" | "reasoning_content"
  >,
): ChatTurnInput {
  return {
    content: message.content,
    runsPayload: message.runs_payload,
    projected: asProjectedTurn(message.projected),
    reasoningContent: message.reasoning_content,
  };
}

export function resolveChatTurn(input: ChatTurnInput): ResolvedChatTurn {
  const projected = normalizeProjected(input.projected);
  const runs = input.runsPayload ?? null;
  // projected present (even with empty process) wins — `asProcessSteps([])`
  // is [] not null, so `fromRuns ?? projected.process` would swallow the
  // multi-agent timeline.
  const process = projected
    ? projected.process
    : (asProcessSteps(runs?.process) ?? []);
  const projectedReasoning = projected?.reasoning ?? "";
  return {
    content: input.content ?? projected?.content ?? "",
    process,
    reasoning: projectedReasoning.trim()
      ? projectedReasoning
      : (input.reasoningContent?.trim() ?? ""),
    projected,
    finishReason: runs?.finish_reason ?? projected?.finishReason ?? null,
    status: projected?.status ?? null,
    outcome: projected?.outcome ?? null,
    error: runs?.error ?? projected?.error ?? null,
    turnWarning: runs?.turn_warning ?? projected?.turnWarning ?? null,
    citations: projected?.citations ?? [],
    runs: projected?.runs ?? [],
    progress: projected?.progress ?? { completed: 0, total: 0 },
    interactions: projected?.interactions ?? [],
    debate: projected?.debate ?? null,
    deliveryStatus: projected?.deliveryStatus ?? null,
  };
}
