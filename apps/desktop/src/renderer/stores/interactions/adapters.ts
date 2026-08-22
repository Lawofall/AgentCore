import { toCeoReview } from "@/lib/ceoReview";
import { parseCheckpointIntent } from "@/lib/checkpointIntent";
import type {
  CheckpointDisplay,
  PlanReviewDisplay,
  TeamPreviewDisplay,
} from "@/stores/conversation/types";
import type { PendingResume, ResumeOrigin } from "@/stores/pausedTurns";
import type {
  AskAssumption,
  AskQuestion,
  PlanReviewPending,
  PlanReviewStep,
} from "@/types/events";
import type { InteractionKind } from "@/types/interactionExt";
import { mapEntryResolution } from "./mapResolution";
import { useInteractionStore } from "./store";
import {
  COLD_RESUME_KINDS,
  type InteractionEntry,
  isColdResumeKind,
} from "./types";

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function arr<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

/** Parse team_preview continue corrections from resolved payload / optimistic resolution. */
function parseTeamPreviewCorrections(
  source: Record<string, unknown> | undefined,
): {
  excluded_run_ids?: string[];
  write_capability_overrides?: Array<{
    run_id: string;
    capability: "text_only";
  }>;
  model_overrides?: Record<
    string,
    { model: string; origin?: "platform" | "byok"; provider_id?: string }
  >;
} {
  if (!source) return {};
  const excluded = arr<unknown>(source.excluded_run_ids).filter(
    (id): id is string => typeof id === "string" && id.length > 0,
  );
  const overrides = arr<unknown>(source.write_capability_overrides)
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const r = row as Record<string, unknown>;
      if (typeof r.run_id !== "string" || !r.run_id) return null;
      if (r.capability !== "text_only") return null;
      return { run_id: r.run_id, capability: "text_only" as const };
    })
    .filter(
      (row): row is { run_id: string; capability: "text_only" } => row != null,
    );
  const model_overrides: Record<
    string,
    { model: string; origin?: "platform" | "byok"; provider_id?: string }
  > = {};
  const rawModels = source.model_overrides;
  if (rawModels && typeof rawModels === "object" && !Array.isArray(rawModels)) {
    for (const [runId, row] of Object.entries(
      rawModels as Record<string, unknown>,
    )) {
      if (!runId || !row || typeof row !== "object") continue;
      const r = row as Record<string, unknown>;
      if (typeof r.model !== "string" || !r.model.trim()) continue;
      model_overrides[runId] = {
        model: r.model.trim(),
        ...(r.origin === "platform" || r.origin === "byok"
          ? { origin: r.origin }
          : {}),
        ...(typeof r.provider_id === "string" && r.provider_id
          ? { provider_id: r.provider_id }
          : {}),
      };
    }
  }
  return {
    ...(excluded.length > 0 ? { excluded_run_ids: excluded } : {}),
    ...(overrides.length > 0 ? { write_capability_overrides: overrides } : {}),
    ...(Object.keys(model_overrides).length > 0 ? { model_overrides } : {}),
  };
}

/** View-model for a pending approval card. */
export interface ApprovalView {
  approvalId: string;
  conversationId: string;
  toolCallId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  resolving: boolean;
}

export function entryToCheckpoint(e: InteractionEntry): CheckpointDisplay {
  const p = e.payload;
  const settlement = mapEntryResolution(e);
  return {
    id: e.id,
    question: str(p.question),
    assumptions: arr<AskAssumption>(p.assumptions),
    questions: arr<AskQuestion>(p.questions),
    intent: parseCheckpointIntent(p.intent),
    ...settlement,
    selected:
      settlement.status === "resolved"
        ? arr<string>(e.resolution?.selected)
        : [],
    ...(p.browser_login === true ? { browserLogin: true as const } : {}),
  };
}

export function entryToPlanReview(e: InteractionEntry): PlanReviewDisplay {
  const p = e.payload;
  return {
    id: e.id,
    steps: arr<PlanReviewStep>(p.steps),
    pending: arr<PlanReviewPending>(p.pending),
    ...mapEntryResolution(e),
    // 主 Agent 把关摘要：live SSE 与 journal 冷加载同走本映射（absent → undefined）。
    ceoReview: toCeoReview(p.ceo_review),
  };
}

export function entryToTeamPreview(e: InteractionEntry): TeamPreviewDisplay {
  const p = e.payload;
  const primitiveRaw = str(p.primitive, "delegate");
  return {
    id: e.id,
    primitive: primitiveRaw === "debate" ? "debate" : "delegate",
    workers: arr<{
      run_id: string;
      role: string;
      task?: string;
      depends_on?: string[];
      form?: string;
      write_capability?: "text_only" | "can_write_files";
      write_capability_label?: string;
      model?: string;
      origin?: "platform" | "byok";
      provider_id?: string;
      target_folder_id?: string;
      target_folder_name?: string;
    }>(p.workers).map((w) => ({
      run_id: w.run_id,
      role: w.role,
      task: w.task ?? "",
      depends_on: w.depends_on ?? [],
      ...(typeof w.form === "string" && w.form ? { form: w.form } : {}),
      ...(w.write_capability === "text_only" ||
      w.write_capability === "can_write_files"
        ? { write_capability: w.write_capability }
        : {}),
      ...(typeof w.write_capability_label === "string" &&
      w.write_capability_label
        ? { write_capability_label: w.write_capability_label }
        : {}),
      ...(typeof w.model === "string" && w.model.trim()
        ? { model: w.model.trim() }
        : {}),
      ...(w.origin === "platform" || w.origin === "byok"
        ? { origin: w.origin }
        : {}),
      ...(typeof w.provider_id === "string" && w.provider_id
        ? { provider_id: w.provider_id }
        : {}),
      ...(typeof w.target_folder_id === "string" && w.target_folder_id.trim()
        ? { target_folder_id: w.target_folder_id.trim() }
        : {}),
      ...(typeof w.target_folder_name === "string" &&
      w.target_folder_name.trim()
        ? { target_folder_name: w.target_folder_name.trim() }
        : {}),
    })),
    tools: arr<string>(p.tools),
    ...(typeof p.headline === "string" && p.headline.trim()
      ? { headline: p.headline.trim() }
      : {}),
    ...(() => {
      const n =
        typeof p.revision === "number" ? p.revision : Number(p.revision);
      const revision = Number.isFinite(n) && n >= 1 ? Math.floor(n) : undefined;
      const revisedFrom =
        typeof p.revised_from === "string" && p.revised_from.trim()
          ? p.revised_from.trim()
          : undefined;
      const revisionNote =
        typeof p.revision_note === "string" && p.revision_note.trim()
          ? p.revision_note.trim()
          : undefined;
      return {
        ...(revision != null ? { revision } : {}),
        ...(revisedFrom ? { revisedFrom } : {}),
        ...(revisionNote ? { revisionNote } : {}),
      };
    })(),
    motion: str(p.motion),
    form: str(p.form),
    sides: arr<{
      key: string;
      name: string;
      stance: string;
      is_subject?: boolean;
      run_id?: string;
      model?: string;
      origin?: "platform" | "byok";
      provider_id?: string;
    }>(p.sides).map((s) => ({
      key: s.key,
      name: s.name,
      stance: s.stance,
      ...(s.is_subject ? { is_subject: true } : {}),
      ...(typeof s.run_id === "string" && s.run_id.trim()
        ? { run_id: s.run_id.trim() }
        : {}),
      ...(typeof s.model === "string" && s.model.trim()
        ? { model: s.model }
        : {}),
      ...(s.origin === "platform" || s.origin === "byok"
        ? { origin: s.origin }
        : {}),
      ...(typeof s.provider_id === "string" && s.provider_id
        ? { provider_id: s.provider_id }
        : {}),
    })),
    maxRounds: typeof p.max_rounds === "number" ? p.max_rounds : 0,
    thorough: p.thorough !== false,
    ...(typeof p.moderator_run_id === "string" && p.moderator_run_id.trim()
      ? { moderatorRunId: p.moderator_run_id.trim() }
      : {}),
    ...(typeof p.moderator_model === "string" && p.moderator_model.trim()
      ? { moderatorModel: p.moderator_model }
      : {}),
    ...(p.moderator_origin === "platform" || p.moderator_origin === "byok"
      ? { moderatorOrigin: p.moderator_origin }
      : {}),
    ...(typeof p.moderator_provider_id === "string" && p.moderator_provider_id
      ? { moderatorProviderId: p.moderator_provider_id }
      : {}),
    ...(p.same_model_debate ? { sameModelDebate: true } : {}),
    ...(() => {
      const raw = p.model_candidates;
      if (!Array.isArray(raw) || raw.length === 0) return {};
      const modelCandidates = raw
        .filter(
          (c): c is Record<string, unknown> =>
            !!c &&
            typeof c === "object" &&
            typeof (c as { model?: unknown }).model === "string",
        )
        .map((c) => {
          const origin =
            c.origin === "platform" || c.origin === "byok"
              ? c.origin
              : "platform";
          return {
            model: String(c.model),
            origin: origin as "platform" | "byok",
            ...(typeof c.provider_id === "string" && c.provider_id
              ? { provider_id: c.provider_id }
              : {}),
            ...(typeof c.label === "string" && c.label
              ? { label: c.label }
              : {}),
            ...(typeof c.side_key === "string" && c.side_key
              ? { side_key: c.side_key }
              : {}),
          };
        });
      return modelCandidates.length > 0 ? { modelCandidates } : {};
    })(),
    ...mapEntryResolution(e),
    ...parseTeamPreviewCorrections(
      (e.resolution ?? undefined) as Record<string, unknown> | undefined,
    ),
  };
}

export function entryToApproval(e: InteractionEntry): ApprovalView {
  const p = e.payload;
  return {
    approvalId: e.id,
    conversationId: e.conversationId,
    toolCallId: str(p.tool_call_id, e.id),
    toolName: str(p.tool_name),
    arguments: (p.arguments ?? {}) as Record<string, unknown>,
    resolving: e.status === "submitting",
  };
}

function matchesMessage(
  e: InteractionEntry,
  conversationId: string,
  messageId: string,
): boolean {
  if (e.conversationId !== conversationId) return false;
  if (!e.messageId || !messageId) return true;
  return e.messageId === messageId;
}

export function listMessageEntries(
  conversationId: string,
  messageId: string,
  kinds?: InteractionKind[],
): InteractionEntry[] {
  const out: InteractionEntry[] = [];
  for (const e of useInteractionStore.getState().byId.values()) {
    if (!matchesMessage(e, conversationId, messageId)) continue;
    if (kinds && !kinds.includes(e.kind)) continue;
    out.push(e);
  }
  return out;
}

export function messageCheckpoints(
  conversationId: string,
  messageId: string,
): CheckpointDisplay[] {
  return listMessageEntries(conversationId, messageId, ["ask_user"]).map(
    entryToCheckpoint,
  );
}

export function messagePlanReviews(
  conversationId: string,
  messageId: string,
): PlanReviewDisplay[] {
  return listMessageEntries(conversationId, messageId, ["plan_review"]).map(
    entryToPlanReview,
  );
}

export function messageTeamPreviews(
  conversationId: string,
  messageId: string,
): TeamPreviewDisplay[] {
  return listMessageEntries(conversationId, messageId, ["team_preview"]).map(
    entryToTeamPreview,
  );
}

/**
 * Exact messageId only. Empty id is not match-all (unlike {@link matchesMessage}),
 * so a leftover continue from another bubble cannot release this turn's graph.
 */
export function teamPreviewsExact(
  entries: Iterable<InteractionEntry>,
  conversationId: string | null,
  messageId: string,
): TeamPreviewDisplay[] {
  if (!conversationId || !messageId) return [];
  const out: TeamPreviewDisplay[] = [];
  for (const e of entries) {
    if (e.kind !== "team_preview") continue;
    if (e.conversationId !== conversationId) continue;
    if (e.messageId !== messageId) continue;
    out.push(entryToTeamPreview(e));
  }
  return out;
}

/**
 * Kickoff-card grant list retired — backend `command=auto` already granted.
 * Approval prompts no longer hide based on a team_preview tools roster.
 */
export function isToolGranted(
  _conversationId: string,
  _toolName: string,
): boolean {
  return false;
}

/**
 * Build a ResumePrompt view-model from an InteractionStore cold pending entry.
 * leftover `team_preview` returns null (kind stays recognizable; no operable VM).
 * Caller supplies the stamped resume key + user context + routing origin.
 */
export function entryToColdResume(
  e: InteractionEntry,
  opts: {
    resumeMessageId: string;
    userMessage: string;
    userMessageId: string;
    origin: ResumeOrigin;
  },
): PendingResume | null {
  if (!isColdResumeKind(e.kind)) return null;
  const kind = e.kind;
  const base = {
    messageId: opts.resumeMessageId,
    conversationId: e.conversationId,
    checkpointId: e.id,
    userMessage: opts.userMessage,
    userMessageId: opts.userMessageId,
    origin: opts.origin,
  };

  if (kind === "ask_user") {
    const cp = entryToCheckpoint(e);
    return {
      ...base,
      kind,
      steps: [],
      pending: [],
      workers: [],
      tools: [],
      primitive: "delegate",
      motion: "",
      form: "",
      sides: [],
      maxRounds: 0,
      thorough: true,
      question: cp.question,
      assumptions: cp.assumptions,
      questions: cp.questions,
      intent: cp.intent,
      ...(cp.browserLogin ? { browserLogin: true as const } : {}),
    };
  }

  if (kind === "plan_review") {
    const pr = entryToPlanReview(e);
    return {
      ...base,
      kind,
      steps: pr.steps,
      pending: pr.pending,
      ceoReview: pr.ceoReview,
      workers: [],
      tools: [],
      primitive: "delegate",
      motion: "",
      form: "",
      sides: [],
      maxRounds: 0,
      thorough: true,
      question: "",
      assumptions: [],
      questions: [],
      intent: "decision",
    };
  }

  // leftover team_preview: kind stays recognizable; no operable resume VM.
  return null;
}

/** Cold pending entries for a conversation (ResumePrompt authority). */
export function listColdPendingEntries(
  conversationId: string,
): InteractionEntry[] {
  return useInteractionStore
    .getState()
    .listPending(conversationId, COLD_RESUME_KINDS);
}
