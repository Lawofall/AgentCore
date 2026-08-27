import { toCeoReview } from "@/lib/ceoReview";
import { parseCheckpointIntent } from "@/lib/checkpointIntent";
import type {
  CheckpointDisplay,
  PlanReviewDisplay,
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
      question: "",
      assumptions: [],
      questions: [],
      intent: "decision",
    };
  }

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
