import type { ErrorAction } from "@/lib/errors";
import type { TimelineMarkerDef } from "@/stores/interactions/registry";
import type {
  Citation,
  ContextBlockWire,
  CostBreakdown,
  GraphAppendPayload,
  ResetReason,
  ToolUseEndPayload,
  ToolUseProgressPayload,
  ToolUseStartPayload,
  UsageBreakdown,
} from "@/types/events";
import type { TurnPhase } from "./turnPhase";
import type { ConversationRuntime, MemoryUpdate, Message } from "./types";

export interface ConversationState {
  currentConversationId: string | null;
  byId: Record<string, ConversationRuntime>;
  /** MRU order of retained conversation slice keys (excludes draft). */
  sliceLruOrder: string[];
  pendingFocus: { conversationId: string; messageId: string } | null;

  setCurrentConversation: (id: string | null) => void;
  dropConversationRuntime: (id: string) => void;
  setMessageWindow: (
    messages: Message[],
    flags: { hasMoreBefore: boolean; hasMoreAfter: boolean },
    conversationId?: string | null,
  ) => void;
  prependMessages: (
    older: Message[],
    hasMoreBefore: boolean,
    conversationId?: string | null,
  ) => void;
  appendNewerMessages: (
    newer: Message[],
    hasMoreAfter: boolean,
    conversationId?: string | null,
  ) => void;
  setLoadingOlder: (v: boolean, conversationId?: string | null) => void;
  setLoadingNewer: (v: boolean, conversationId?: string | null) => void;
  setMemoryUpdates: (
    updates: MemoryUpdate[],
    conversationId?: string | null,
  ) => void;
  addMemoryUpdate: (
    update: MemoryUpdate,
    conversationId?: string | null,
  ) => void;
  addMessage: (message: Message, conversationId?: string | null) => void;
  /** `replace`（attach 增量重放）：`chunk` 是末尾未闭合正文块的全文，换块而非追加。 */
  appendToLastMessage: (
    chunk: string,
    conversationId?: string | null,
    opts?: { replace?: boolean },
  ) => void;
  resetStreamingContent: (
    reason: ResetReason,
    conversationId?: string | null,
  ) => void;
  /** `replace`：同 {@link ConversationState.appendToLastMessage}，作用于思考通道。 */
  appendReasoningToLastMessage: (
    chunk: string,
    conversationId?: string | null,
    opts?: { replace?: boolean },
  ) => void;
  setComposingTool: (
    tool: { toolName: string; chars: number } | null,
    conversationId?: string | null,
  ) => void;
  setTraceIdOnLastMessage: (
    traceId: string,
    conversationId?: string | null,
  ) => void;
  setServerMessageIdOnLastMessage: (
    messageId: string,
    conversationId?: string | null,
  ) => void;
  /**
   * Resume = same-turn continuation: flip the paused assistant back to streaming
   * under the server turn id. Returns the bubble id, or null if not found.
   */
  resumePausedAssistant: (
    serverMessageId: string,
    conversationId?: string | null,
  ) => string | null;
  /**
   * 新回合 `message_start`（陌生 message_id）复用尾部流式占位气泡时，清掉占位上
   * 属于上一段生命的残留（正文/思考/过程/撰写中工具）——对齐 conformanceFold 的
   * message_start 语义（message_id 变化 ⇒ 空正文、空过程时间线）。同 id 的
   * pause→resume 不走这里（那是 resumePausedAssistant 的保留正文路径）。
   */
  resetAssistantForNewTurn: (
    serverMessageId: string,
    conversationId?: string | null,
  ) => void;
  addProcessTool: (
    payload: ToolUseStartPayload,
    conversationId?: string | null,
  ) => void;
  endProcessTool: (
    payload: ToolUseEndPayload,
    conversationId?: string | null,
  ) => void;
  setProcessToolPhase: (
    payload: ToolUseProgressPayload,
    conversationId?: string | null,
  ) => void;
  attachCitationsToLastMessage: (
    citations: Citation[],
    conversationId?: string | null,
  ) => void;
  attachEvidenceLedgerToLastMessage: (
    payload: {
      delta?: import("@/types/events").TurnEvidenceLedgerEntry[];
      entries?: import("@/types/events").TurnEvidenceLedgerEntry[] | null;
      cited_ids?: string[] | null;
    },
    conversationId?: string | null,
  ) => void;
  recordTurnWarning: (warning: string, conversationId?: string | null) => void;
  stampPendingTurnWarning: (conversationId?: string | null) => void;
  attachCostToLastMessage: (
    cost: CostBreakdown,
    conversationId?: string | null,
  ) => void;
  attachTurnMetaToLastMessage: (
    meta: {
      usage?: UsageBreakdown;
      rounds?: number;
      durationMs?: number;
      finishReason?: string;
      collab?: import("@/types/events").TurnCollabMetrics;
      outcome?: "ok" | "partial" | "paused" | "error" | null;
      teamBatch?: import("@/types/events").TeamBatchStatus;
    },
    conversationId?: string | null,
  ) => void;
  attachErrorToLastMessage: (
    error: {
      code: string;
      message: string;
      context?: {
        upstream_status?: number;
        upstream_body_preview?: string | null;
        retry_attempts?: number;
        empty_diagnosis?: string;
        body_kind?: string;
        base_url?: string;
        vendor_code?: string | null;
        model?: string | null;
        profile?: string | null;
        tool_count?: number | null;
        credential_source?: "user" | "platform" | string | null;
        /** 额度恢复 / 配额重置的绝对时刻（ISO8601 UTC）——红卡按本机时区成文。 */
        recovery_at?: string | null;
        reset_at?: string | null;
      };
    },
    conversationId?: string | null,
  ) => void;
  stampCheckpointMarker: (
    checkpointId: string,
    conversationId?: string | null,
  ) => void;
  stampUserInterjectionMarker: (
    interjectionId: string,
    conversationId?: string | null,
  ) => void;
  stampPlanReviewMarker: (
    checkpointId: string,
    conversationId?: string | null,
  ) => void;
  stampTeamPreviewMarker: (
    checkpointId: string,
    conversationId?: string | null,
  ) => void;
  /** Registry-driven timeline marker stamp (approval / escalation / …). */
  stampTimelineMarker: (
    marker: TimelineMarkerDef,
    id: string,
    conversationId?: string | null,
  ) => void;
  createAssistantMessage: (conversationId?: string | null) => string;
  finalizeLastMessage: (conversationId?: string | null) => void;
  updateMessage: (
    id: string,
    update: Partial<Message>,
    conversationId?: string | null,
  ) => void;
  removeMessage: (id: string, conversationId?: string | null) => void;
  truncateAfter: (id: string, conversationId?: string | null) => void;
  reconcileLastTurn: (
    userMessageId: string,
    conversationId?: string | null,
  ) => void;
  /** Mark user + paired assistant with local outbox sync hint (desktop-only). */
  setTurnSyncStatus: (
    userMessageId: string,
    syncStatus: Message["syncStatus"],
    conversationId?: string | null,
  ) => void;
  setLastAssistantExecutionId: (
    executionId: string,
    conversationId?: string | null,
  ) => void;
  /** 跨回合同图追加锚点——盖在【追加回合】最新助手气泡的 process 上。 */
  stampGraphAppend: (
    payload: GraphAppendPayload,
    conversationId?: string | null,
  ) => void;
  setCaptainContext: (
    blocks: ContextBlockWire[],
    conversationId?: string | null,
  ) => void;
  setGenerating: (v: boolean, conversationId?: string | null) => void;
  /** 真正换 id 时清掉即将显示切片的残留 messageFocus；pendingFocus 不动。 */
  switchConversation: (id: string | null) => void;
  /**
   * Explicit idle-slice drop (tests / diagnostics). Production terminal SSE
   * (`message_end` / `error`) no longer calls this — idle eviction is LRU-only
   * on {@link ConversationState.switchConversation}.
   */
  releaseBackgroundSlice: (conversationId: string) => void;
  setAbort: (a: AbortController | null, conversationId?: string | null) => void;
  setTurnPhase: (phase: TurnPhase, conversationId?: string | null) => void;
  /** Desktop: last turn path — `sidecar` | `cloud_bridge` | null (see ConversationRuntime). */
  setExecutionVia: (
    via: ConversationRuntime["executionVia"],
    conversationId?: string | null,
  ) => void;
  /** Live-only workspace_lock_wait UX（不得静默等锁）. */
  setWaitingForWorkspaceLock: (
    waiting: boolean,
    conversationId?: string | null,
  ) => void;
  /** Explicit hard cancel of the in-flight turn (disconnect alone does not cancel). */
  stopGeneration: () => void;
  setError: (
    message: string,
    retry: (() => void) | null,
    conversationId?: string | null,
    action?: ErrorAction | null,
  ) => void;
  clearError: (conversationId?: string | null) => void;
  focusMessage: (id: string, conversationId?: string | null) => void;
  requestMessageFocus: (conversationId: string, messageId: string) => void;
  clearPendingFocus: () => void;
}

export type ConversationSet = (
  partial:
    | Partial<ConversationState>
    | ((
        state: ConversationState,
      ) => Partial<ConversationState> | ConversationState),
  replace?: false,
) => void;

export type ConversationGet = () => ConversationState;

export type PatchConversation = (
  conversationId: string | null | undefined,
  update: (rt: ConversationRuntime) => Partial<ConversationRuntime> | null,
) => void;
