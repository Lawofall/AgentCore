import {
  EXECUTION_HARVEST_ORIGIN,
  isExecutionHarvestMessage,
} from "@/lib/executionHarvest";
import { ensureTimelineMarkersFromJournal } from "@/lib/foldMessageLane";
import { logEvent } from "@/lib/log";
import { promoteScalarContentIntoProcess } from "@/lib/processTimeline";
import {
  attestedKindFromEvents,
  parseTurnOutcomeKind,
} from "@/lib/turnOutcome";
import { api } from "@/services/api";
import { persistOpenedCache } from "@/services/offlineCache";
import { surfaceResumeFromAssistant } from "@/services/resume";
import { clearLastEventId } from "@/services/streamConversation";
import { hasLocalConversationStream } from "@/services/turns/streamOwnership";
import {
  type MemoryUpdate,
  type Message,
  getRuntime,
  isMessageWindowResident,
  isMessageWindowStrictlyRicher,
  overlayIncomingWithRicherExisting,
  useConversationStore,
} from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { hydrateInteractionsFromJournal } from "@/stores/interactions";
import type { components } from "@/types/api.generated";
import type {
  ContextBlockWire,
  ProcessStep,
  SSEEvent,
  UsageBreakdown,
} from "@/types/events";

type Schemas = components["schemas"];
/** A window of messages (cursor-windowed, oldest-first) from the REST endpoint. */
type BackendMessageListResponse = Schemas["MessageListResponse"];

/**
 * A persisted message as the REST endpoint shapes it. Mirrors the generated
 * `MessageDetail`, but types `runs.events` as the client's {@link SSEEvent}
 * union (the OpenAPI carries it as opaque JSON — SSE/event payloads are exempt
 * from the generated-types rule, API 开发规范) so the team-graph replay fold is
 * typed end to end.
 */
export interface BackendMessage {
  id: string;
  conversation_id: string;
  role: string;
  content: string | null;
  reasoning_content: string | null;
  /** The turn's log correlation id (messages.trace_id) — replayed onto
   * `message.traceId` so a reloaded bubble can copy it for one-step log lookup. */
  trace_id?: string | null;
  attachments?: {
    name: string;
    path: string;
    truncated: boolean;
    kind?: "file" | "dir" | "conversation";
    workspace_path?: string | null;
    conversation_id?: string | null;
  }[];
  /** Conversation-page ``@`` role chips (soft mention; not attachment kind). */
  agent_mentions?: { agent_id: string; role: string }[];
  citations?: {
    url: string;
    title: string;
    snippet?: string;
    site?: string;
    id?: string | null;
    date?: string | null;
    tier?: string | null;
    query?: string | null;
    deep_read?: boolean | null;
    registrant?: string | null;
    citable?: boolean | null;
  }[];
  /** 回合调研台账（引用即出处 P1, DERIVED）：messages.evidence_ledger。 */
  evidence_ledger?: {
    id: string;
    url?: string;
    title?: string;
    snippet?: string;
    site?: string;
    date?: string;
    tier?: string;
    query?: string;
    deep_read?: boolean;
    registrant?: string;
    citable?: boolean;
  }[];
  /** 回复反馈 (点赞/点踩, 对话基础功能补齐): the user's rating on this assistant reply
   * (messages.feedback column) — "up" | "down" | null(未评价). Replayed onto
   * `message.feedback` so a reloaded bubble shows the rating the user gave. */
  feedback?: "up" | "down" | null;
  /** Persisted turn replay payload. `events` is a multi-agent turn's ordered
   * run/tool SSE events (replayed through the same fold as the live stream to
   * rebuild the team graph on reload, §9.3); `process` is a single-agent turn's
   * 思考·正文·工具 inline timeline (前端UX设计.md §一B). `run_processes` is the
   * per-worker-run timeline map (对称 CEO process). null for user / plain turns. (Opaque JSON
   * in the OpenAPI — SSE/event payloads are exempt from the generated-types rule.) */
  runs?: {
    events: SSEEvent[];
    finish_reason: string | null;
    process?: ProcessStep[] | null;
    /** Per-run ProcessStep[] (run_id → steps); reload seeds run detail timelines. */
    run_processes?: Record<string, ProcessStep[]> | null;
    /** 收到的上下文 · CEO 侧 (上下文传递可视化 通道①): the captain's `run_context` blocks,
     * persisted turn-level (the captain is the bubble above the graph, present even in pure
     * chat where `events` is empty). Replayed onto `message.captainContext` on reload. */
    captain_context?: ContextBlockWire[] | null;
    /** 报错回合's terminal error (Tier 2 a): projected from the journal's `turn_end` outcome
     * fact so the inline error card replays on reload (live, the error rode a transport-only
     * `error` SSE event). Replayed onto `message.error` — same `{code, message}` shape the
     * live handler attaches. null for a clean turn. */
    error?: { code: string; message: string } | null;
    /** 预检警告（P2 DURABLE）：journaled turn_warning lifted for plain-chat reload. */
    turn_warning?: string | null;
    /** List GET may drop bulky events; false → fetch GET …/messages/{id} for graph. */
    events_complete?: boolean;
    /** 裸聊自动建文件夹（§5.4）：journal 仍投影；对话内不再画落点条，故不抬到气泡。 */
    auto_folder?: { folder_id: string; name: string } | null;
  } | null;
  /** 回合 token 用量 (Tier 2 重载持久化): the turn's token snapshot in the ledger short-key
   * shape, projected server-side from the row's `usage` column. Replayed onto
   * `message.usage` so the bubble's meta row caption replays on reload — live, it rode
   * `message_end`. null for user rows and no-spend (errored/empty) turns without a
   * structured error. Failed empty turns may still project usage with ``error`` only. */
  usage?:
    | (UsageBreakdown & {
        error?: { code: string; message: string } | null;
        /** Persist column may carry finish_reason; REST UsageBreakdown strips it. */
        finish_reason?: string | null;
      })
    | null;
  /** Progressive assistant-row lifecycle (``usage.status``). Overlay / salvage
   * criterion — ``running`` hydrates as streaming partial; ``incomplete`` as
   * interrupted unless usage/runs already cancelled. */
  status?: "running" | "complete" | "incomplete" | "failed" | null;
  /** Cold-path pause latch (``usage.paused``). Write keeps ``status=running``;
   * when true, hydrate as paused (not streaming) — finishReason=paused. */
  paused?: boolean | null;
  /** 消息来源（如 execution_harvest）；写入 usage JSON，读路径投影到此字段. */
  origin?: string | null;
  /** 曾中断恢复（``usage.recovered``）：本回合崩过、由租约清扫重驱原地跑完. */
  recovered?: boolean | null;
  /** 回合轮次 (Tier 2 重载持久化): ReAct rounds the turn ran, projected from the same column.
   * Replayed onto `message.rounds`; the bubble surfaces「N 轮」only when > 1. null for
   * user / pre-feature rows. */
  rounds?: number | null;
  /** 回合墙钟用时 (ms)：与 message_end.duration_ms 同锚；重载自 usage JSON 投影。 */
  duration_ms?: number | null;
  /** Server-attested turn result (`turn_metrics.status` / `message_end.outcome`). */
  outcome?: ("ok" | "partial" | "paused" | "error") | null;
  /** 协作质量 (学·度量 §2.5): orchestration signals for 诊断模式; nested in usage column. */
  collab?: {
    boundary_yields: number;
    scope_signals: number;
    revises: number;
    escalations: number;
    audit_drops?: number;
  } | null;
  /** 本回合团队状态（journal 派生；没派工是 no_batch）。 */
  team_batch?: import("@/types/events").TeamBatchStatus | null;
  /** 回合 ¥ 成本 (P2 DERIVED)：messages.cost 列；重载 footer 直接用。 */
  cost?: import("@/services/usage").CostBreakdown | null;
  created_at: string;
}

/** A loaded slice of a conversation, plus the flags that drive infinite scroll. */
export interface MessageWindow {
  messages: Message[];
  total: number;
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
  /** 记忆更新对话内可见 (§1.6): the conversation-tail「记忆已更新」cards. Backend returns
   * these ONLY for the latest window (the cards sit after the last message); empty on
   * scroll-up / around pages. */
  memoryUpdates: MemoryUpdate[];
}

/** Map a persisted `memory_updates` row (REST `MemoryUpdateView`) to the client's
 * domain {@link MemoryUpdate} for the conversation-tail card. */
export function toMemoryUpdate(m: Schemas["MemoryUpdateView"]): MemoryUpdate {
  return {
    id: m.id,
    createdAt: m.created_at,
    anchorAt: m.anchor_at ?? null,
    kind: m.kind,
    summary: m.summary ?? null,
    items: (m.items ?? []).map((it) => ({
      action: it.action,
      file: it.file,
      section: it.section,
      scope: it.scope,
      content: it.content,
      target: it.target,
      projectId: it.project_id ?? null,
    })),
  };
}

/** The execution (plan) id of a reloaded multi-agent turn — the first
 * `run_plan`'s id in the persisted journal. null for user / single-agent turns
 * (no journal, or a journal with no plan), which then render as plain bubbles. */
function executionIdOf(events: SSEEvent[]): string | null {
  const plan = events.find((e) => e.type === "run_plan");
  const id = (plan?.payload as { execution_id?: string } | undefined)
    ?.execution_id;
  return id ?? null;
}

/** Reload finishReason: cancelled on usage/runs wins over incomplete → interrupted. */
function hydrateFinishReason(
  paused: boolean,
  status: BackendMessage["status"],
  runsFinish: string | null | undefined,
  usageFinish: string | null | undefined,
): string | undefined {
  if (paused) return "paused";
  if (runsFinish === "cancelled" || usageFinish === "cancelled") {
    return "cancelled";
  }
  if (runsFinish) return runsFinish;
  if (usageFinish) return usageFinish;
  if (status === "incomplete") return "interrupted";
  return undefined;
}

/** Map a persisted message row to the client's domain {@link Message}, rebuilding
 * its team graph / checkpoint projections from the journal so a reloaded turn
 * renders exactly like its live one did. */
export function toMessage(m: BackendMessage): Message {
  const events = m.runs?.events ?? [];
  const executionId = executionIdOf(events);
  // Journal → InteractionStore (P2 unified store; reload path).
  // Cold/hot cards render from InteractionStore — no Message field dual-write.
  if (events.length > 0) {
    hydrateInteractionsFromJournal(m.conversation_id, m.id, events);
  }
  // Cold-path pause latch: InteractionStore is the live ResumePrompt authority.
  // Mirror recovery shell into pausedTurns when the persisted row is still paused
  // so reopen / offline hydrate keeps origin routing + shell fallback even if
  // /recovery raced empty. Live cards no longer require message_end → surface.
  const paused = Boolean(m.paused);
  if (paused && m.role === "assistant" && events.length > 0) {
    const priorUser = [...getRuntime(m.conversation_id).messages]
      .reverse()
      .find((msg) => msg.role === "user");
    surfaceResumeFromAssistant(
      m.conversation_id,
      { id: m.id, serverMessageId: m.id },
      "server",
      { content: priorUser?.content, id: priorUser?.id },
    );
  }
  // steps — now for single-agent AND multi-agent turns (统一团队时间线). Single-agent
  // tool-less turns synthesize one reasoning step from reasoning_content.
  const baseProcess: ProcessStep[] | undefined =
    m.runs?.process ??
    (!executionId && m.reasoning_content
      ? [{ kind: "reasoning", text: m.reasoning_content }]
      : undefined);
  // 补标记（时间线一期）: backfill positional markers the journal implies (`team` /
  // `team_preview` / `checkpoint` / `ask` / `plan_review`) so the invariant「有交互卡
  // 必有时间线标记」holds on reload — the bottom-stack fallback is gone, an unmarked
  // card would silently vanish. Dedup no-ops when the persisted process already
  // carries them (the normal case).
  // 协作图应在 CEO 回复下方: if mid-run hydrate left narration on `content` while
  // process only has team/markers (stream_segments / outbox edge), promote into a
  // content step before the first team marker.
  const markedProcess: ProcessStep[] | undefined =
    events.length > 0
      ? ensureTimelineMarkersFromJournal(baseProcess, events)
      : baseProcess;
  const contentText = m.content ?? "";
  const process: ProcessStep[] | undefined =
    m.role === "assistant" && contentText
      ? promoteScalarContentIntoProcess(markedProcess, contentText)
      : markedProcess;
  // On reload the row `id` IS the server message_id. Stamp `serverMessageId` so
  // resume guards (`isClientOnlyResumeKey`) match the live path (message_start stamp)
  // and do not treat a hydrated assistant as client-only.
  const role = m.role === "assistant" ? "assistant" : "user";
  // P4: running → stream-style partial; incomplete / interrupted finish → interrupted chip.
  // Cold-path pause latch: write keeps status=running + paused=true; hydrate as paused
  // (not streaming) so reopen does not setGenerating / spinner forever.
  // usage/runs already cancelled must not map incomplete → interrupted (user-stop).
  const status = m.status ?? null;
  const finishReason = hydrateFinishReason(
    paused,
    status,
    m.runs?.finish_reason,
    m.usage?.finish_reason,
  );
  const isStreaming = role === "assistant" && status === "running" && !paused;
  // Keep the journal whenever events exist — classic turns may have DURABLE
  // `user_interjection` (and delivery_status) without a `run_plan`. Previously
  // dropping `runs` when `executionId` was null meant nothing called
  // hydrateFromJournal for classic reload (多 Agent still hydrates via
  // InlineTeamGraph).
  const journal =
    events.length > 0
      ? {
          events,
          finishReason: finishReason ?? "stop",
          runProcesses: m.runs?.run_processes ?? null,
          eventsComplete: m.runs?.events_complete !== false,
        }
      : undefined;
  // Classic cold path only: multi-agent keeps hydrate timing on InlineTeamGraph
  // mount — do not double-fold here when executionId (run_plan) is present.
  // Incomplete list journals must not fold a skeleton graph (full GET on mount).
  if (!executionId && journal && journal.eventsComplete !== false) {
    useExecutionStore.getState().hydrateFromJournal(m.id, journal);
  }
  const mapped: Message = {
    id: m.id,
    ...(role === "assistant" ? { serverMessageId: m.id } : {}),
    role,
    content: contentText,
    reasoning: m.reasoning_content ?? undefined,
    // 关联气泡↔日志: replay the turn's trace_id so a reloaded bubble's「复制排查包」
    // includes it for log_timeline.py --trace.
    traceId: m.trace_id ?? undefined,
    process,
    createdAt: m.created_at,
    // Stamp the plan id so the bubble renders its inline team graph; the journal
    // below lets that graph replay the turn (executionId null for classic / no plan).
    executionId,
    runs: journal,
    // 结束原因 chip (Tier 2 c): surface the persisted finish_reason turn-level so a
    // single-agent abnormal turn (max_rounds / degraded / unproductive) replays its
    // chip on reload too — the bubble reads `finishReason ?? runs?.finishReason`.
    // A clean turn carries no journal → undefined → no chip. (Multi-agent also
    // keeps its `runs.finishReason` above; this is redundant but harmless there.)
    finishReason,
    outcome:
      parseTurnOutcomeKind(m.outcome) ??
      attestedKindFromEvents(events) ??
      undefined,
    status,
    // 报错回合 error card (Tier 2 a): replay the inline error card from the persisted
    // outcome, mirroring the live `error` event handler's `{code, message}` attach.
    // Prefer runs.error (journal); fall back to usage.error when journal is sparse
    // (空泡族根因重设计 — REST 已投影 usage.error).
    error: m.runs?.error ?? m.usage?.error ?? undefined,
    // 回合 token 用量 + 轮次 (Tier 2 重载): replay the bubble's meta row from the persisted
    // turn snapshot, mirroring the live `attachTurnMetaToLastMessage` stamp — usage is
    // already the ledger short-key shape (normalized server-side), rounds drives the
    // 「N 轮」caption. Both undefined for user / no-spend turns → no meta row (live parity).
    usage: m.usage ?? undefined,
    rounds: m.rounds ?? undefined,
    durationMs: m.duration_ms ?? undefined,
    collab: m.collab ?? undefined,
    teamBatch: m.team_batch ?? undefined,
    // 回复反馈 (点赞/点踩): replay the persisted rating so a reloaded bubble shows the
    // user's thumbs; null server-side → null (未评价).
    feedback: m.feedback ?? null,
    // 回合 ¥ 成本 (P2 DERIVED)：messages.cost 列；重载 footer 直接用（hover 明细仍走 GET …/cost）。
    cost: m.cost ?? undefined,
    // 预检警告（P2）：runs 投影抬升的 turn_warning → 消息横幅。
    turnWarning: m.runs?.turn_warning ?? undefined,
    // 收到的上下文 · CEO 侧 (上下文传递可视化 通道①): turn-level, so it replays independently
    // of the team graph — present on pure-chat reloads (empty `events`) too.
    captainContext: m.runs?.captain_context?.length
      ? m.runs.captain_context
      : undefined,
    isStreaming,
    agentMentions: m.agent_mentions?.length
      ? m.agent_mentions.map((a) => ({
          agentId: a.agent_id,
          role: a.role,
        }))
      : undefined,
    attachments: m.attachments?.length
      ? m.attachments.map((a) => ({
          id: crypto.randomUUID(),
          name: a.name,
          path: a.path,
          truncated: a.truncated,
          kind: a.kind ?? "file",
          workspacePath: a.workspace_path ?? undefined,
          conversationId: a.conversation_id ?? undefined,
        }))
      : undefined,
    citations: m.citations?.length
      ? m.citations.map((c) => ({
          url: c.url,
          title: c.title,
          snippet: c.snippet,
          site: c.site,
          id: c.id ?? undefined,
          date: c.date ?? undefined,
          tier: c.tier ?? undefined,
          query: c.query ?? undefined,
          deep_read: c.deep_read ?? undefined,
          registrant: c.registrant ?? undefined,
          citable: c.citable ?? undefined,
        }))
      : undefined,
    evidenceLedger: m.evidence_ledger?.length ? m.evidence_ledger : undefined,
    // REST origin（如 execution_harvest）；缺省时下方前缀兜底。
    origin: m.origin ?? undefined,
    // 曾中断恢复：崩溃重驱把这条回合跑完了，气泡上如实挂标记。
    ...(m.recovered ? { recovered: true } : {}),
  };
  // 异步团队收口：优先 REST origin；正文前缀兜底旧数据。
  if (
    isExecutionHarvestMessage({
      role: mapped.role,
      content: mapped.content,
      origin: mapped.origin,
    })
  ) {
    mapped.origin = EXECUTION_HARVEST_ORIGIN;
  }
  return mapped;
}

/**
 * Whether a freshly loaded message window should paint live generating chrome.
 * Driven solely by {@link Message.isStreaming} (toMessage already maps cold-path
 * ``paused`` out of streaming). Ghost interrupted remains a separate recovery branch.
 */
export function shouldSetGeneratingOnHydrate(
  messages: { isStreaming?: boolean }[],
): boolean {
  return messages.at(-1)?.isStreaming === true;
}

/** How to window a conversation's messages — mutually exclusive (the backend
 * checks `around` → `before` → `after` → latest in that order). */
export interface MessageWindowQuery {
  /** Center the window on this message id (search-hit jump, load-around B). */
  around?: string;
  /** Load the page strictly older than this ISO cursor (scroll up). */
  before?: string;
  /** Load the page strictly newer than this ISO cursor (scroll down). */
  after?: string;
  /** Page size per direction (default = backend default of 100). */
  limit?: number;
}

/**
 * Fetch one window of a conversation's messages.
 *
 * No params → the latest window (conversation open). `around`/`before`/`after`
 * drive the search-hit jump and infinite scroll. The returned `hasMore*` flags
 * tell the caller whether more remain in each direction.
 */
export async function fetchMessageWindow(
  conversationId: string,
  query: MessageWindowQuery = {},
  signal?: AbortSignal,
): Promise<MessageWindow> {
  const params = new URLSearchParams();
  if (query.around) params.set("around", query.around);
  if (query.before) params.set("before", query.before);
  if (query.after) params.set("after", query.after);
  if (query.limit != null) params.set("limit", String(query.limit));
  const qs = params.toString();
  const res = await api.get<BackendMessageListResponse>(
    `/v1/conversations/${conversationId}/messages${qs ? `?${qs}` : ""}`,
    signal ? { signal } : undefined,
  );
  const rows = res.data as unknown as BackendMessage[];
  return {
    messages: rows.map((m) => toMessage(m)),
    total: res.total,
    hasMoreBefore: res.has_more_before,
    hasMoreAfter: res.has_more_after,
    memoryUpdates: (res.memory_updates ?? []).map(toMemoryUpdate),
  };
}

const _fullRunsInflight = new Map<string, Promise<Message | null>>();
const _fullRunsFailed = new Set<string>();

/** Test seam: clear in-flight / failed GET-one-message journal fetches. */
export function resetEnsureFullMessageRunsForTests(): void {
  _fullRunsInflight.clear();
  _fullRunsFailed.clear();
}

function storeMessageByProjectionId(
  conversationId: string,
  messageId: string,
): Message | undefined {
  const msgs =
    useConversationStore.getState().byId[conversationId]?.messages ?? [];
  return msgs.find(
    (m) => m.id === messageId || m.serverMessageId === messageId,
  );
}

/**
 * When the list GET slimmed ``runs.events``, fetch the full ``MessageDetail``
 * and write it onto the resident bubble so graph / turn-detail can fold.
 * No-op when already complete (including legacy cache without the flag).
 */
export async function ensureFullMessageRuns(
  conversationId: string,
  messageId: string,
  signal?: AbortSignal,
): Promise<Message | null> {
  const key = `${conversationId}:${messageId}`;
  if (_fullRunsFailed.has(key)) return null;
  const existing = storeMessageByProjectionId(conversationId, messageId);
  if (existing?.runs?.eventsComplete !== false) {
    return existing ?? null;
  }
  const pending = _fullRunsInflight.get(key);
  if (pending) return pending;

  const task = (async (): Promise<Message | null> => {
    try {
      const row = await api.get<BackendMessage>(
        `/v1/conversations/${conversationId}/messages/${messageId}`,
        signal ? { signal } : undefined,
      );
      const full = toMessage(row);
      const store = useConversationStore.getState();
      const bubble = storeMessageByProjectionId(conversationId, messageId);
      if (bubble && store.byId[conversationId]) {
        store.updateMessage(
          bubble.id,
          {
            runs: full.runs,
            process: full.process,
            executionId: full.executionId,
          },
          conversationId,
        );
      }
      if (full.runs && full.runs.eventsComplete !== false) {
        useExecutionStore.getState().hydrateFromJournal(messageId, full.runs);
      }
      return full;
    } catch {
      _fullRunsFailed.add(key);
      return null;
    } finally {
      _fullRunsInflight.delete(key);
    }
  })();
  _fullRunsInflight.set(key, task);
  return task;
}

/** ISO `createdAt` of a conversation slice's oldest / newest loaded message, or
 * null when empty. The cursors infinite scroll pages from. */
function edgeCursors(conversationId: string): {
  oldest: string | null;
  newest: string | null;
} {
  const slice = useConversationStore.getState().byId[conversationId]?.messages;
  if (!slice || slice.length === 0) return { oldest: null, newest: null };
  return {
    oldest: slice[0].createdAt,
    newest: slice[slice.length - 1].createdAt,
  };
}

/**
 * Load the page just older than what's loaded and prepend it (scroll up).
 * No-op when nothing more remains or a load is already in flight, so a burst of
 * scroll events collapses into one request.
 */
export async function loadOlderMessages(conversationId: string): Promise<void> {
  const store = useConversationStore.getState();
  const rt = store.byId[conversationId];
  if (!rt || !rt.hasMoreBefore || rt.loadingOlder) return;
  const { oldest } = edgeCursors(conversationId);
  if (!oldest) return;
  store.setLoadingOlder(true, conversationId);
  try {
    const win = await fetchMessageWindow(conversationId, { before: oldest });
    useConversationStore
      .getState()
      .prependMessages(win.messages, win.hasMoreBefore, conversationId);
  } catch {
    /* best-effort: a failed page just leaves the older button to retry on scroll */
  } finally {
    useConversationStore.getState().setLoadingOlder(false, conversationId);
  }
}

/**
 * Load the page just newer than what's loaded and append it (scroll down).
 * Only meaningful after a load-around jump left newer history unloaded
 * (`hasMoreAfter`); a no-op at the live head.
 */
export async function loadNewerMessages(conversationId: string): Promise<void> {
  const store = useConversationStore.getState();
  const rt = store.byId[conversationId];
  if (!rt || !rt.hasMoreAfter || rt.loadingNewer) return;
  const { newest } = edgeCursors(conversationId);
  if (!newest) return;
  store.setLoadingNewer(true, conversationId);
  try {
    const win = await fetchMessageWindow(conversationId, { after: newest });
    useConversationStore
      .getState()
      .appendNewerMessages(win.messages, win.hasMoreAfter, conversationId);
  } catch {
    /* best-effort */
  } finally {
    useConversationStore.getState().setLoadingNewer(false, conversationId);
  }
}

/**
 * Soft background refresh (harvest / detached catch-up): applies the full
 * whole-window write gates, including active+hasMoreAfter (do not yank the
 * user off mid-history). Intentional snap (composer send / 「跳到最新」) omit
 * this flag so hasMoreAfter + non-dominating latest windows may still apply.
 */
export type LoadLatestWindowOpts = {
  softRefresh?: boolean;
  /** Page-lifecycle abort for the window GET only — never forwarded to follow backfill. */
  signal?: AbortSignal;
};

/**
 * Warm open write policy (消息窗写入契约 step 3):
 * - local stream pumping → keep live slice (no network window replace)
 * - destination (pendingFocus / ?msg=) → keep current slice for jump/load-around
 * - else (sidebar reopen / A→B→A) → explicit latest snap (not softRefresh)
 */
export type WarmOpenAction = "skip_generating" | "keep_anchor" | "snap_latest";

export function decideWarmOpenAction(opts: {
  hasLocalStream: boolean;
  hasDestination: boolean;
}): WarmOpenAction {
  if (opts.hasLocalStream) return "skip_generating";
  if (opts.hasDestination) return "keep_anchor";
  return "snap_latest";
}

/**
 * Reload the latest window, replacing whatever is on screen. Used to snap back
 * to the live head before a new turn when the user is reading a historical
 * window (a search-hit jump left `hasMoreAfter`), so the turn appends at the
 * true tail rather than into a mid-conversation gap.
 */
export async function loadLatestWindow(
  conversationId: string,
  opts: LoadLatestWindowOpts = {},
): Promise<boolean> {
  const softRefresh = opts.softRefresh === true;

  const reject = (
    action: string,
    fields: Record<string, unknown> = {},
  ): false => {
    const s = useConversationStore.getState();
    const rt = s.byId[conversationId];
    logEvent("info", "conversation.slice_diag", {
      action,
      conversation_id: conversationId,
      active_id: s.currentConversationId,
      soft_refresh: softRefresh,
      message_count: rt?.messages.length ?? 0,
      is_generating: rt?.isGenerating ?? false,
      has_more_after: rt?.hasMoreAfter ?? false,
      ...fields,
    });
    return false;
  };

  const storeBefore = useConversationStore.getState();
  if (
    !isMessageWindowResident(
      storeBefore.currentConversationId,
      storeBefore.byId,
      conversationId,
    )
  ) {
    return reject("reject_not_resident");
  }
  const before = storeBefore.byId[conversationId];
  if (hasLocalConversationStream(conversationId)) {
    return reject("reject_generating");
  }
  if (
    softRefresh &&
    storeBefore.currentConversationId === conversationId &&
    before?.hasMoreAfter
  ) {
    return reject("reject_active_has_more_after");
  }

  const win = await fetchMessageWindow(conversationId, {}, opts.signal);
  const store = useConversationStore.getState();
  if (
    !isMessageWindowResident(
      store.currentConversationId,
      store.byId,
      conversationId,
    )
  ) {
    return reject("reject_not_resident", { after_count: win.messages.length });
  }
  const rt = store.byId[conversationId];
  if (hasLocalConversationStream(conversationId)) {
    return reject("reject_generating", { after_count: win.messages.length });
  }
  if (
    softRefresh &&
    store.currentConversationId === conversationId &&
    rt?.hasMoreAfter
  ) {
    return reject("reject_active_has_more_after", {
      after_count: win.messages.length,
    });
  }

  const existing = rt?.messages ?? [];
  const snapPastHistory =
    !softRefresh &&
    store.currentConversationId === conversationId &&
    (rt?.hasMoreAfter ?? false);
  if (
    existing.length > 0 &&
    !snapPastHistory &&
    !isMessageWindowStrictlyRicher(win.messages, existing)
  ) {
    return reject("reject_not_richer", {
      before_count: existing.length,
      after_count: win.messages.length,
    });
  }

  logEvent("info", "conversation.slice_diag", {
    action: "load_latest_window",
    conversation_id: conversationId,
    active_id: store.currentConversationId,
    soft_refresh: softRefresh,
    before_count: before?.messages.length ?? 0,
    after_count: win.messages.length,
    has_more_after: win.hasMoreAfter,
    has_more_before: win.hasMoreBefore,
    replaced_while_background:
      store.currentConversationId !== null &&
      store.currentConversationId !== conversationId,
  });
  store.setMessageWindow(
    win.messages,
    { hasMoreBefore: win.hasMoreBefore, hasMoreAfter: win.hasMoreAfter },
    conversationId,
  );
  clearLastEventId(conversationId);
  // Latest window owns the tail cards; replace them (older/around pages return none).
  store.setMemoryUpdates(win.memoryUpdates, conversationId);
  // Trusted write only — reject paths above return without persisting.
  if (win.messages.length > 0) {
    void persistOpenedCache(conversationId, win.messages, win.memoryUpdates, {
      hasMoreBefore: win.hasMoreBefore,
      hasMoreAfter: win.hasMoreAfter,
    });
  }
  return true;
}

/**
 * Jump the conversation to a specific message (search-hit "命中必达").
 *
 * If the message is already in the loaded window, just scroll to it. Otherwise
 * fetch a window centered on it (load-around B), swap it in, then focus — so a hit
 * outside the latest 100 still lands precisely. Assumes `conversationId` is (or is
 * becoming) the active conversation; the caller navigates there first.
 */
export async function jumpToMessage(
  conversationId: string,
  messageId: string,
  signal?: AbortSignal,
): Promise<void> {
  const store = useConversationStore.getState();
  const rt = store.byId[conversationId];
  // Permalink / search may carry the server turn id while the live bubble still
  // keys on a client UUID — match either, then focus by the store bubble id.
  const present = rt?.messages.find(
    (m) => m.id === messageId || m.serverMessageId === messageId,
  );
  if (present) {
    store.focusMessage(present.id, conversationId);
    return;
  }
  try {
    const win = await fetchMessageWindow(
      conversationId,
      { around: messageId },
      signal,
    );
    const after = useConversationStore.getState();
    // The user may have navigated away while the window loaded — only swap it in
    // if this conversation is still the one on screen.
    if ((after.currentConversationId ?? "") !== conversationId) return;
    if (win.messages.length === 0) return;
    const existing = after.byId[conversationId]?.messages ?? [];
    const merged = overlayIncomingWithRicherExisting(win.messages, existing);
    const hitInMerged = merged.find(
      (m) => m.id === messageId || m.serverMessageId === messageId,
    );
    if (!hitInMerged) return;
    after.setMessageWindow(
      merged,
      { hasMoreBefore: win.hasMoreBefore, hasMoreAfter: win.hasMoreAfter },
      conversationId,
    );
    clearLastEventId(conversationId);
    // The tail cards belong only to the live tail; an around-window has none, so this
    // clears any cards left from the latest view (they'd otherwise float after the
    // historical window). They return on the next latest-window load.
    after.setMemoryUpdates(win.memoryUpdates, conversationId);
    // Focus on the next frame so the bubbles have rendered before we scroll.
    requestAnimationFrame(() => {
      const msgs =
        useConversationStore.getState().byId[conversationId]?.messages ?? [];
      const hit = msgs.find(
        (m) => m.id === messageId || m.serverMessageId === messageId,
      );
      if (hit)
        useConversationStore.getState().focusMessage(hit.id, conversationId);
    });
  } catch {
    /* message gone / not owned — leave the conversation as-is */
  }
}

/**
 * Set / clear the user's 点赞/点踩 on an assistant reply (回复反馈). Optimistic: the
 * bubble flips immediately, then persists; a failed PATCH reverts to the prior rating
 * and rethrows so the caller can toast. `feedback` is "up" / "down" to rate, or null to
 * clear (clicking the active side again toggles it off).
 */
export async function setMessageFeedback(
  conversationId: string,
  messageId: string,
  feedback: "up" | "down" | null,
): Promise<void> {
  const store = useConversationStore.getState();
  const prev =
    store.byId[conversationId]?.messages.find((m) => m.id === messageId)
      ?.feedback ?? null;
  store.updateMessage(messageId, { feedback }, conversationId);
  try {
    await api.patch(
      `/v1/conversations/${conversationId}/messages/${messageId}/feedback`,
      { feedback },
    );
  } catch (err) {
    useConversationStore
      .getState()
      .updateMessage(messageId, { feedback: prev }, conversationId);
    throw err;
  }
}
