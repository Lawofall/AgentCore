import { toCeoReview } from "@/lib/ceoReview";
import {
  type AskUiIntent,
  parseCheckpointIntent,
} from "@/lib/checkpointIntent";
import type { components } from "@/types/api.generated";
import type {
  AskAssumption,
  AskOption,
  AskQuestion,
  CeoReviewSummary,
  PlanReviewPending,
  PlanReviewStep,
} from "@/types/events";
import { create } from "zustand";

type PausedTurnSummary = components["schemas"]["PausedTurnSummary"];
type SuspensionKind = components["schemas"]["SuspensionKind"];

/** Where the durable paused frame lives — drives resume routing in {@link runResume}. */
export type ResumeOrigin = "sidecar" | "server";

/**
 * 这次打开对话，挂起帧 recovery 问到了哪一步。
 * `unresolved` = 还没落地（硬刷新默认）；`ready` = 问到了（空列表也算）；
 * `failed` = 请求失败，unknown ≠ idle。
 */
export type ColdOpenRecoveryState = "unresolved" | "ready" | "failed";

const ALL_ORIGINS: readonly ResumeOrigin[] = ["sidecar", "server"];

/**
 * 观察序号（单调递增），用来判定「某次 /recovery 快照看得见哪些本地壳」。
 *
 * 服务端**先落盘挂起帧、再发 `*_required`**（见 ask_user 工具：`saved` 为真才
 * `sink.emit(required)`），所以一次快照只要是在某张卡浮现**之后**才发起的，就必然
 * 读得到它的 durable 帧——这样的快照回空 = 帧真被消费掉了，壳该清。反过来，快照在
 * 卡浮现前就上了路，回空只说明它太早，那正是 live pause 抢跑竞态，不许清。
 *
 * 用序号而不是时间戳：不受时钟精度 / 回拨影响，同一毫秒内的先后也分得清。
 */
let observationSeq = 0;

/** 盖在新浮现的壳 / IX 卡上；与 {@link beginPausedSnapshot} 同一条序号。 */
export const nextObservationSeq = (): number => ++observationSeq;

/**
 * 取一次快照的观察起点——**发请求之前**调用。此后才进入本地的壳，这次快照都看不见，
 * 因而不受它的空结果处置。
 */
export function beginPausedSnapshot(): number {
  return observationSeq;
}

/**
 * A turn that paused at a plan_review / ask_user checkpoint, was DURABLY persisted,
 * then lost its live SSE — client disconnect / server restart (结构化挂起 2b). On
 * conversation reopen the client loads these from the recovery snapshot (GET /recovery)
 * and offers 继续 / 调整 / 停止, each driving POST .../resume to continue on a fresh stream.
 *
 * Mirrors the approvals store: one entry per paused turn, tagged with its
 * `conversationId` so several conversations can each hold their own pending
 * resumes; the card above the composer renders only the active conversation's.
 *
 * `kind` selects the card the {@link ResumePrompt} renders: plan_review reviews the
 * finished `steps` + gated `pending`; ask_user re-asks the unified card content
 * (`question` + `assumptions` / `questions`).
 * The unused set is empty for the other kind.
 */
export interface PendingResume {
  /** The paused turn's assistant message_id — the resume key, and the id the
   * resumed reply reuses when it finally persists. */
  messageId: string;
  conversationId: string;
  checkpointId: string;
  /** Which suspend point this turn paused at — drives the resume card variant. */
  kind: SuspensionKind;
  /** The original user request that started the paused turn (for context). */
  userMessage: string;
  /** Client-minted id of the user bubble (pinned on pause write-back). */
  userMessageId: string;
  /** plan_review: the just-completed checkpoint step(s) under review. */
  steps: PlanReviewStep[];
  /** plan_review: the downstream nodes gated behind the pause. */
  pending: PlanReviewPending[];
  /** plan_review: 主 Agent 暂停前的把关摘要（absent = 旧帧 / 无摘要 → 不渲染）。 */
  ceoReview?: CeoReviewSummary;
  /** ask_user: the framing / opening line (always shown). */
  question: string;
  /** ask_user: 起步计划 read-only chips (低影响决策，开场常见). */
  assumptions: AskAssumption[];
  /** ask_user: the askable items (途中岔路通常一个；开场可多个). */
  questions: AskQuestion[];
  /** ask_user chrome intent after {@link parseCheckpointIntent}. */
  intent: AskUiIntent;
  /** ask_user browser_login=true → login card；点「打开浏览器」才 reveal 右坞. */
  browserLogin?: boolean;
  /** Where the durable frame lives — drives {@link runResume} sidecar vs server routing. */
  origin: ResumeOrigin;
  /** 这张壳进入本地时的{@link beginPausedSnapshot 观察序号}；由 store 自己盖，外部构造
   * 的 {@link PendingResume}（如 InteractionStore 直投的卡）没有 → 视作最早。 */
  surfacedSeq?: number;
}

/** `steps` / `pending` arrive as loose JSON dicts (backend ``list[dict]``); map
 * them to the known display shapes, tolerating any missing field. */
const toSteps = (raw: PausedTurnSummary["steps"]): PlanReviewStep[] =>
  (raw ?? []).map((s) => ({
    run_id: String(s.run_id ?? ""),
    role: String(s.role ?? ""),
    summary: String(s.summary ?? ""),
  }));

const toPending = (raw: PausedTurnSummary["pending"]): PlanReviewPending[] =>
  (raw ?? []).map((p) => ({
    run_id: String(p.run_id ?? ""),
    role: String(p.role ?? ""),
  }));

/** ask_user rich fields arrive as loose JSON dicts (backend ``list[dict]``); map
 * them to the typed display shapes the unified card reads, tolerating missing keys.
 * The backend already normalized + capped + id'd them (ask_user._normalize_*). */
const toAssumptions = (
  raw: PausedTurnSummary["assumptions"],
): AskAssumption[] =>
  (raw ?? []).map((a, i) => ({
    id: String(a.id ?? `a${i}`),
    label: String(a.label ?? ""),
    value: String(a.value ?? ""),
  }));

/** Options rehydrate as `{label, detail?, action?, well_known?, target_name?}` from the backend. */
const toOptions = (raw: unknown): AskOption[] =>
  Array.isArray(raw)
    ? raw.map((o) => {
        const obj = (o ?? {}) as Record<string, unknown>;
        const wellKnown =
          obj.well_known === "desktop" ||
          obj.well_known === "downloads" ||
          obj.well_known === "documents"
            ? obj.well_known
            : undefined;
        const targetName =
          typeof obj.target_name === "string" && obj.target_name.trim()
            ? obj.target_name.trim()
            : undefined;
        return {
          label: String(obj.label ?? ""),
          ...(obj.detail ? { detail: String(obj.detail) } : {}),
          ...(obj.action === "open_local_project" ||
          obj.action === "register_local_project" ||
          obj.action === "bind_local_folder" ||
          obj.action === "grant_organize_folder" ||
          obj.action === "grant_attach_folder"
            ? {
                action: obj.action as
                  | "open_local_project"
                  | "register_local_project"
                  | "bind_local_folder"
                  | "grant_organize_folder"
                  | "grant_attach_folder",
              }
            : {}),
          ...(wellKnown ? { well_known: wellKnown } : {}),
          ...(targetName ? { target_name: targetName } : {}),
        };
      })
    : [];

const toQuestions = (raw: PausedTurnSummary["questions"]): AskQuestion[] =>
  (raw ?? []).map((q, i) => ({
    id: String(q.id ?? `q${i}`),
    prompt: String(q.prompt ?? ""),
    kind: q.kind === "text" ? "text" : "choice",
    options: toOptions(q.options),
    multiple: Boolean(q.multiple),
    default: String(q.default ?? ""),
  }));

const toIntent = (raw: unknown): AskUiIntent => parseCheckpointIntent(raw);

/** One recovery-frame summary tagged with where its durable frame lives. */
export type PausedTurnEntry = {
  summary: PausedTurnSummary;
  origin: ResumeOrigin;
};

interface PausedTurnState {
  pending: PendingResume[];
  /** Per-conversation recovery probe for cold-open paint (missing key = unresolved). */
  openRecovery: Record<string, ColdOpenRecoveryState>;
  markOpenRecovery: (
    conversationId: string,
    state: ColdOpenRecoveryState,
  ) => void;
  /**
   * Replace one conversation's pending resumes (from the recovery snapshot on reopen),
   * leaving other conversations' entries untouched. Each entry carries its own
   * {@link ResumeOrigin} so a mixed cloud+sidecar session routes resume correctly.
   *
   * 快照只对**它看得见的**壳有处置权：本地壳被清掉需同时满足「它那一路真被问到了」
   * 与「它在快照发起前就已浮现」。两条各挡一种误清——请求失败 ≠ 帧没了；卡刚浮现时
   * 在飞的快照本就来不及看见它（live pause 抢跑竞态）。
   */
  setForConversation: (
    conversationId: string,
    entries: PausedTurnEntry[],
    opts?: {
      /** {@link beginPausedSnapshot} 在发请求前取的观察起点；缺省 = 「刚看过」。 */
      since?: number;
      /** 这次真问到了哪些落盘源（请求成功）；缺省 = 两路都问到了。 */
      confirmed?: readonly ResumeOrigin[];
    },
  ) => void;
  /** 挂起即收口 (②): add/replace ONE turn's resume entry the moment its LIVE stream ENDS
   * at a checkpoint (message_end finish_reason=paused). Built from the *_required payload
   * already folded onto the bubble — no /recovery round-trip — so it reproduces offline in
   * #/preview. Idempotent by messageId, so a later reopen's setForConversation (the same
   * frame, re-read from the backend) simply replaces it rather than stacking a duplicate. */
  addLiveResume: (entry: PendingResume) => void;
  /** Drop one paused turn (it is being / has been resumed). Idempotent. */
  remove: (messageId: string) => void;
  /** Drop the paused turn whose checkpoint just settled on the LIVE stream
   * (checkpoint_resolved / plan_review_resolved). The server deletes the durable
   * frame on an in-process resolve, so mirror that here — otherwise a 待恢复 card
   * left over from a duplicate surface lingers and 404s when clicked (its frame is
   * already gone). Keyed by checkpoint_id (what the resolve event carries).
   * Idempotent; a no-op when no entry matches. */
  removeByCheckpoint: (checkpointId: string) => void;
  /** Forget pending resumes. Pass a conversationId to drop only that
   * conversation's; omit for a full reset (e.g. logout / tests). */
  clear: (conversationId?: string) => void;
  /** Re-key a live-surfaced frame after message_start stamps the server id
   * (card may have been keyed by the client bubble id when pause raced stamp). */
  rekeyMessageId: (fromMessageId: string, toMessageId: string) => void;
}

function entryFromSummary(
  conversationId: string,
  s: PausedTurnSummary,
  origin: ResumeOrigin,
): PendingResume {
  return {
    messageId: s.message_id,
    conversationId,
    checkpointId: s.checkpoint_id,
    kind: s.kind,
    userMessage: s.user_message ?? "",
    userMessageId: s.user_message_id ?? "",
    steps: toSteps(s.steps),
    pending: toPending(s.pending),
    // REST 快照尚未列该字段进 schema；宽松读，后端带了就透传（absent → undefined）。
    ceoReview: toCeoReview((s as { ceo_review?: unknown }).ceo_review),
    question: s.question ?? "",
    assumptions: toAssumptions(s.assumptions),
    questions: toQuestions(s.questions),
    intent: toIntent((s as { intent?: unknown }).intent),
    ...((s as { browser_login?: unknown }).browser_login === true
      ? { browserLogin: true as const }
      : {}),
    origin,
  };
}

export const usePausedTurnStore = create<PausedTurnState>((set) => ({
  pending: [],
  openRecovery: {},

  markOpenRecovery: (conversationId, state) =>
    set((prev) => ({
      openRecovery: { ...prev.openRecovery, [conversationId]: state },
    })),

  setForConversation: (conversationId, entries, opts) => {
    const since = opts?.since ?? observationSeq;
    const confirmed = opts?.confirmed ?? ALL_ORIGINS;
    const incoming = entries.map(({ summary, origin }) => ({
      ...entryFromSummary(conversationId, summary, origin),
      surfacedSeq: nextObservationSeq(),
    }));
    const restated = new Set(incoming.map((p) => p.messageId));
    set((state) => ({
      pending: [
        ...state.pending.filter((p) => {
          if (p.conversationId !== conversationId) return true;
          if (restated.has(p.messageId)) return false; // 快照带了新版本
          if (!confirmed.includes(p.origin)) return true; // 那一路没问到 = 未知
          return (p.surfacedSeq ?? 0) > since; // 快照发起后才浮现 → 它看不见
        }),
        ...incoming,
      ],
    }));
  },

  addLiveResume: (entry) => {
    const surfaced = { ...entry, surfacedSeq: nextObservationSeq() };
    set((state) => ({
      pending: [
        ...state.pending.filter((p) => p.messageId !== surfaced.messageId),
        surfaced,
      ],
    }));
  },

  remove: (messageId) =>
    set((state) => ({
      pending: state.pending.filter((p) => p.messageId !== messageId),
    })),

  removeByCheckpoint: (checkpointId) =>
    set((state) => {
      const pending = state.pending.filter(
        (p) => p.checkpointId !== checkpointId,
      );
      return pending.length === state.pending.length ? state : { pending };
    }),

  clear: (conversationId) =>
    set((state) => {
      if (conversationId === undefined) {
        return { pending: [], openRecovery: {} };
      }
      const openRecovery = { ...state.openRecovery };
      delete openRecovery[conversationId];
      return {
        pending: state.pending.filter(
          (p) => p.conversationId !== conversationId,
        ),
        openRecovery,
      };
    }),

  rekeyMessageId: (fromMessageId, toMessageId) =>
    set((state) => {
      if (!fromMessageId || !toMessageId || fromMessageId === toMessageId) {
        return state;
      }
      let changed = false;
      const pending = state.pending.map((p) => {
        if (p.messageId !== fromMessageId) return p;
        changed = true;
        return { ...p, messageId: toMessageId };
      });
      if (!changed) return state;
      // Drop duplicates if recovery already keyed the server id.
      const seen = new Set<string>();
      const deduped: PendingResume[] = [];
      for (const p of pending) {
        if (seen.has(p.messageId)) continue;
        seen.add(p.messageId);
        deduped.push(p);
      }
      return { pending: deduped };
    }),
}));
