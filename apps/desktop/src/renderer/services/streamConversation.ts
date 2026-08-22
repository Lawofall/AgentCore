import { clientHeaders } from "@/lib/clientBuildInfo";
import { StreamError, streamErrorFromResponse } from "@/lib/errors";
import { logEvent } from "@/lib/log";
import { bearerAuthHeader, sessionCredentials } from "@/lib/sessionAuth";
import {
  ApiError,
  BASE_URL,
  captureCsrf,
  getCsrfHeaders,
  isReplayableCsrfRejection,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";
import type { PlanReviewUserDecision } from "@/services/planReview";
import {
  dispatchSSEEvent,
  flushPendingContent,
  flushPendingFrames,
} from "@/services/sse/dispatch";
import { traceTurnMilestone } from "@/services/turnTrace";
import {
  beginLocalConversationStream,
  claimPrimaryStream,
  releasePrimaryStream,
} from "@/services/turns/streamOwnership";
import { getRuntime } from "@/stores/conversation";
import {
  enterTurnStreaming,
  throwIfCannotOpenStream,
} from "@/stores/conversation/turnPhaseActions";
import { useExecutionStore } from "@/stores/execution";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import type { MessageStartPayload, SSEEvent } from "@/types/events";
import { unstable_batchedUpdates } from "react-dom";
import { resetPartialTurnForReplay } from "./turns/replayReset";

/** SSE comment after attach journal replay (+ hot re-hang); mirrors server
 * ``sse._ATTACH_CAUGHT_UP``. Not an EventType — pump-level only. */
export const ATTACH_CAUGHT_UP_COMMENT = "attach-caught-up";

/** Max wait for response headers (connect + server accept). Distinct from {@link pumpSSE}'s
 *  idle timeout, which only applies once the body is streaming. */
const CONNECT_TIMEOUT_MS = 30_000;

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** `fetch` with a connect-phase ceiling; user `signal` abort still propagates as AbortError.
 * 同步拒绝已 abort 的 signal，避免连接超时窗口内仍发出请求。 */
async function fetchWithConnectTimeout(
  init: (signal: AbortSignal) => Promise<Response>,
  userSignal?: AbortSignal,
): Promise<Response> {
  if (userSignal?.aborted) {
    throw new DOMException("Aborted", "AbortError");
  }
  const fetchAc = new AbortController();
  const timer = setTimeout(() => fetchAc.abort(), CONNECT_TIMEOUT_MS);
  const onUserAbort = () => fetchAc.abort();
  userSignal?.addEventListener("abort", onUserAbort);
  try {
    return await init(fetchAc.signal);
  } catch (err) {
    if (isAbortError(err)) {
      if (userSignal?.aborted) throw err;
      throw new StreamError("network");
    }
    throw err;
  } finally {
    clearTimeout(timer);
    userSignal?.removeEventListener("abort", onUserAbort);
  }
}

/** Latest journal seq **folded/dispatched** on this conversation (for Last-Event-ID). */
const lastEventIds = new Map<string, string>();
/** Parsed ``id:`` stamped onto the event object; committed only after fold/dispatch. */
const pendingEventIds = new WeakMap<SSEEvent, string>();

function stampParsedEventId(event: SSEEvent, sseId: string | null): void {
  if (sseId) pendingEventIds.set(event, sseId);
}

/** Advance the resume cursor after this event has been folded or dispatched. */
export function commitFoldedEventId(
  conversationId: string,
  event: SSEEvent,
): void {
  const id = pendingEventIds.get(event);
  if (id) lastEventIds.set(conversationId, id);
}

/** Dispatch then commit the stamped journal seq (live tail / catch-up fold). */
export function dispatchFoldedSseEvent(
  event: SSEEvent,
  ctx: Parameters<typeof dispatchSSEEvent>[1],
): void {
  dispatchSSEEvent(event, ctx);
  commitFoldedEventId(ctx.conversationId, event);
}

/**
 * Force the active SSE pump for ``conversationId`` to die as a transport drop
 * (same {@link StreamError} ``network`` path as ``sse.idle_stall``), so the turn
 * catcher rejoins rather than treating it as an honest user stop.
 *
 * Used when a cloud workspace settle exhausts transient retries — the same blip
 * often strands later ``workspace_op_required`` frames on a half-dead pump.
 */
const pumpForceDrop = new Map<string, () => void>();

export function forceSseTransportDrop(conversationId: string): boolean {
  const drop = pumpForceDrop.get(conversationId);
  if (!drop) return false;
  drop();
  return true;
}

/** Read the cursor used for precise stream resume. */
export function peekLastEventId(conversationId: string): string | undefined {
  return lastEventIds.get(conversationId);
}

export function clearLastEventId(conversationId: string): void {
  lastEventIds.delete(conversationId);
}

/**
 * Drain an SSE response body, routing every `data:` event through
 * `dispatchSSEEvent` (or a custom ``onEvent``). Shared by the POST turn channel
 * (send / regenerate / resume / midFlight) and the GET re-attach channel
 * (实时重连续看 C1 · slice 1b) — every SSE consumer folds events through the one
 * dispatch, so a live stream, a reload, and a reconnect all rebuild identical state.
 *
 * Applies the idle stall watchdog: the backend heart-beats every ~15s while a
 * turn thinks, so a live connection always delivers bytes; total silence for the
 * timeout means the socket is dead (server / proxy dropped it), so we cancel and
 * raise a retriable network error rather than hang. This is an *idle* timeout,
 * never a total-duration cap — a long turn that keeps streaming (or just
 * heart-beating) is never cut off.
 *
 * Tracks the latest **folded** SSE ``id:`` (journal seq) per conversation for
 * ``Last-Event-ID`` resume. Parse-time ids stay pending until fold/dispatch;
 * yielding or dropping an unfolded buffer does not advance or clear the cursor.
 *
 * ``onComment`` receives SSE comment payloads (text after ``:``), used by attach
 * catch-up (``attach-caught-up``) — heartbeats (``ping``) are ignored by callers.
 */
export async function pumpSseBody(
  response: Response,
  conversationId: string,
  onEvent?: (event: SSEEvent) => void,
  onComment?: (comment: string) => void,
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) return;

  // 缺省 = 唯一 dispatch 通道（live / reload / reconnect）；调用方可注入 onEvent
  //（如 midFlight 缓冲至主路空闲再 fold；出队插泡由 messageStream 读 started 帧）。
  const deliver =
    onEvent ??
    ((event: SSEEvent) =>
      dispatchFoldedSseEvent(event, { conversationId, source: "server" }));

  const decoder = new TextDecoder();
  let buffer = "";
  /** Most recent SSE ``id:`` in the current frame (reset each blank-line frame). */
  let frameId: string | null = null;

  const IDLE_TIMEOUT_MS = 60_000;
  let pendingReject: ((err: unknown) => void) | null = null;

  const forceTransportDrop = (): void => {
    logEvent("warn", "sse.forced_transport_drop", {
      conversation_id: conversationId,
    });
    void reader.cancel().catch(() => {});
    const reject = pendingReject;
    pendingReject = null;
    reject?.(new StreamError("network"));
  };
  pumpForceDrop.set(conversationId, forceTransportDrop);

  const readChunk = (): ReturnType<typeof reader.read> =>
    new Promise((resolve, reject) => {
      pendingReject = reject;
      const timer = setTimeout(() => {
        if (pendingReject === reject) pendingReject = null;
        // L3：空闲 60s 无字节 → 泵自杀；此后 workspace_op 可能无人履行。
        logEvent("warn", "sse.idle_stall", {
          conversation_id: conversationId,
          idle_timeout_ms: IDLE_TIMEOUT_MS,
        });
        void reader.cancel().catch(() => {});
        reject(new StreamError("network"));
      }, IDLE_TIMEOUT_MS);
      reader.read().then(
        (r) => {
          clearTimeout(timer);
          if (pendingReject === reject) pendingReject = null;
          resolve(r);
        },
        (e) => {
          clearTimeout(timer);
          if (pendingReject === reject) pendingReject = null;
          reject(e);
        },
      );
    });

  try {
    while (true) {
      const { done, value } = await readChunk();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (line === "") {
          frameId = null;
          continue;
        }
        if (line.startsWith(":")) {
          // Heartbeat (``: ping``) or attach boundary (``: attach-caught-up``).
          onComment?.(line.slice(1).trim());
          continue;
        }
        if (line.startsWith("id:")) {
          const id = line.slice(3).trim();
          if (id) frameId = id;
          continue;
        }
        if (!line.startsWith("data: ")) continue;
        try {
          const event = JSON.parse(line.slice(6)) as SSEEvent;
          stampParsedEventId(event, frameId);
          deliver(event);
        } catch {
          /* malformed event — skip */
        }
      }
    }
  } finally {
    if (pumpForceDrop.get(conversationId) === forceTransportDrop) {
      pumpForceDrop.delete(conversationId);
    }
  }
}

/**
 * Fold one buffered attach catch-up段 in a single React batch — resetting this turn's
 * local state first **only when the segment head says so** (实时重连续看 · 流式回复
 * 持久化 §3.6 · P3).
 *
 * 服务端在段首 ``message_start`` 上表态，客户端照做，绝不自己猜：
 *
 * - 带 ``full_replay`` = 全量段（本段就是这一回合的全部）→ 原位清空本回合正文 / 过程 /
 *   执行槽（**保留气泡 id**，避免换泡把已画 Markdown 卸掉），再整段重折，否则正文折两遍。
 *   一次折仍是为了协作图：已完成 worker 不得再演一遍 running→completed。
 * - 不带（增量段 / 没有段首的段）=「你手里那半场是对的，往后接」→ 一个字都不许清。断线重连
 *   时客户端手上的上半场只在自己内存里，清了就永远回不来了——服务端这一段只带游标之后的事实。
 *   另端刚开的新回合同理（本端从没见过它，清只会抹掉上一回合的团队图）。
 *
 * 段首认段内首个 ``message_start``（``resume_settled`` 之类的 lead 帧可能排在它前面）。空段
 * 没有段首，也就没有「全量」可言：服务端刻意不给空段盖标记，正是不让客户端清空后无米下锅。
 *
 * 一次性成批写出，避免逐帧把已完成的 worker 再演一遍 running→completed。
 */
export function foldAttachSegment(
  conversationId: string,
  segment: SSEEvent[],
  extras?: { skipQueuedTurnUserBubble?: boolean },
): void {
  const head = segment.find((e) => e.type === "message_start");
  const fullReplay =
    (head?.payload as MessageStartPayload | undefined)?.full_replay === true;
  unstable_batchedUpdates(() => {
    // 没有锚点（消息窗里还没有那条用户提问）时 reset 落空 → 仍清尾泡执行槽兜底。
    if (fullReplay) {
      clearLastEventId(conversationId);
      const replayMessageId = (head?.payload as MessageStartPayload | undefined)
        ?.message_id;
      resetPartialTurnForReplay(conversationId, replayMessageId);
      const last = getRuntime(conversationId).messages.at(-1);
      if (last?.role === "assistant") {
        const { clearExecution } = useExecutionStore.getState();
        clearExecution(last.id);
        if (last.serverMessageId && last.serverMessageId !== last.id) {
          clearExecution(last.serverMessageId);
        }
      }
    }
    for (const event of segment) {
      dispatchFoldedSseEvent(event, {
        conversationId,
        source: "server",
        replay: true,
        skipQueuedTurnUserBubble: extras?.skipQueuedTurnUserBubble,
      });
    }
    flushPendingContent(conversationId);
    flushPendingFrames(conversationId);
  });
}

/** Outcome of a re-attach attempt (执行与请求解耦 C1 · slice 1b). */
export type AttachOutcome = "attached" | "none";

/**
 * Re-attach to a conversation's in-flight turn and 续看 it live (C1 · slice 1b).
 *
 * Always sends ``Last-Event-ID`` (last journal seq on this conversation's SSE, or
 * ``0`` when none) — the server reads it as「我看到这里为止」and answers with either
 * the whole turn or just the facts after it (流式回复持久化 §3.6 · P3).
 *
 * Catch-up: buffer journal replay (+ hot re-hang) until ``: attach-caught-up``, then
 * one-shot fold through {@link foldAttachSegment} — the段首 decides whether that fold
 * clears this turn's local state first. **调用方不得抢在 attach 之前清屏**：段是不是
 * 全量只有服务端知道，抢清后若来的是增量段，本回合上半场就永久没了。
 *
 * Older servers without the comment flush the buffer when the stream ends
 * (degraded: still one paint, no live boundary).
 *
 * **回合级** attach（不带 ``follow``）：无 live run 时服务端回 204 → ``"none"``，回合
 * 收口即断流。调用方（``rejoinLiveTurn``）靠这个 204 判「回合已结束、去读持久化」，
 * 所以这里绝不能改成对话级长订阅——那条路见 ``turns/conversationFollow``。
 */
export async function attachConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AttachOutcome> {
  throwIfCannotOpenStream(conversationId, signal);
  enterTurnStreaming(conversationId);
  const primaryToken = claimPrimaryStream(conversationId);
  const releaseLocalStream = beginLocalConversationStream(conversationId);

  const doFetch = async (signal: AbortSignal): Promise<Response> => {
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
      ...clientHeaders(),
      ...bearerAuthHeader(),
    };
    // 恒带：``0`` = 本端没有游标（服务端据此回整段），否则报出看到的最后一个 journal seq。
    headers["Last-Event-ID"] = lastEventIds.get(conversationId) ?? "0";
    const response = await fetch(
      `${BASE_URL}/v1/conversations/${conversationId}/stream`,
      {
        method: "GET",
        credentials: sessionCredentials(),
        headers,
        signal,
      },
    );
    captureCsrf(response); // token rides every response, streams included
    return response;
  };

  try {
    let response = await fetchWithConnectTimeout(doFetch, signal);
    if (response.status === 401) {
      const outcome = await tryRefresh();
      if (outcome === "renewed") {
        response = await fetchWithConnectTimeout(doFetch, signal);
      } else if (outcome === "auth_dead") {
        notifyUnauthorized();
        throw new StreamError("auth");
      } else {
        throw new StreamError("network");
      }
      if (response.status === 401) {
        notifyUnauthorized();
        throw new StreamError("auth");
      }
    }
    if (response.status === 204) {
      return "none";
    }
    if (!response.ok) {
      throw await streamErrorFromResponse(response);
    }

    const catchUp: SSEEvent[] = [];
    let catchingUp = true;
    await pumpSseBody(
      response,
      conversationId,
      (event) => {
        if (catchingUp) {
          catchUp.push(event);
          return;
        }
        dispatchFoldedSseEvent(event, { conversationId, source: "server" });
      },
      (comment) => {
        if (!catchingUp) return;
        if (comment !== ATTACH_CAUGHT_UP_COMMENT) return;
        catchingUp = false;
        foldAttachSegment(conversationId, catchUp);
        catchUp.length = 0;
      },
    );
    // Legacy server: no caught-up comment — flush whatever we buffered (whole stream).
    if (catchingUp && catchUp.length > 0) {
      foldAttachSegment(conversationId, catchUp);
    }

    if (getRuntime(conversationId).isGenerating) {
      throw new StreamError("network");
    }
    return "attached";
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    if (err instanceof StreamError) throw err;
    throw new StreamError("network");
  } finally {
    flushPendingContent(conversationId);
    flushPendingFrames(conversationId);
    releasePrimaryStream(conversationId, primaryToken);
    releaseLocalStream();
  }
}

/** 本发是否已提交一条回合。云端见过 `turn_saved` 则置位；sidecar 见 outbox flush 成功。 */
export type TurnCommitReport = { committed: boolean };

/**
 * POST to an SSE endpoint and route every event through `dispatchSSEEvent`.
 *
 * 发送即有流：本端点恒返回 SSE（含 in-flight 时先发 ``turn_queued`` 再同连接
 * 续流；插话（经典/协调）为 ``user_interjection`` 短确认流）。不再有 HTTP 202 JSON。
 */
async function runMessageStream(
  path: string,
  body: string,
  conversationId: string,
  signal?: AbortSignal,
  turnCommit?: TurnCommitReport,
): Promise<void> {
  clearInteractionPrompts(conversationId);
  throwIfCannotOpenStream(conversationId, signal);
  enterTurnStreaming(conversationId);
  const primaryToken = claimPrimaryStream(conversationId);
  const releaseLocalStream = beginLocalConversationStream(conversationId);

  const doFetch = async (signal: AbortSignal): Promise<Response> => {
    const response = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      credentials: sessionCredentials(),
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...clientHeaders(),
        ...bearerAuthHeader(),
        ...getCsrfHeaders("POST"),
      },
      body,
      signal,
    });
    captureCsrf(response);
    return response;
  };

  try {
    traceTurnMilestone(conversationId, "fetch_start", { path });
    let response = await fetchWithConnectTimeout(doFetch, signal);
    traceTurnMilestone(conversationId, "fetch_response", {
      status: response.status,
      ok: response.ok,
    });
    // One replay for the whole call, shared by both recoveries below: a server
    // that keeps refusing costs one extra attempt, never a loop of turn starts.
    let replayed = false;
    if (response.status === 401) {
      const outcome = await tryRefresh();
      if (outcome === "renewed") {
        response = await fetchWithConnectTimeout(doFetch, signal);
        replayed = true;
      } else if (outcome === "auth_dead") {
        notifyUnauthorized();
        throw new StreamError("auth");
      } else {
        throw new StreamError("network");
      }
      if (response.status === 401) {
        notifyUnauthorized();
        throw new StreamError("auth");
      }
    }

    // Re-sending a POST that *starts a turn* is safe only because of what the
    // verdict proves: the CSRF middleware refused this request before any handler
    // ran, so there is no turn to double-start, and the refusal carried a usable
    // replacement token (`doFetch` absorbed it, and rebuilds its headers per
    // attempt so the replay presents it). A 403 that withheld that token is the
    // server declining to re-arm us and must stay a failure — the client never
    // second-guesses that from the status alone. See {@link isReplayableCsrfRejection}.
    if (!replayed && response.status === 403) {
      const refusal = new ApiError(
        response.status,
        await response.clone().text(),
        response.headers,
      );
      if (isReplayableCsrfRejection(response, refusal)) {
        logEvent("info", "auth.csrf_replay", { path, via: "message_stream" });
        replayed = true;
        response = await fetchWithConnectTimeout(doFetch, signal);
      }
    }

    if (!response.ok) {
      throw await streamErrorFromResponse(response);
    }
    // 发送即有流：成功体必须是 SSE。202 在 fetch 里仍算 ok，但契约已退役——
    // 显式失败，避免把 JSON 体当 SSE 静默读完再误判断流。
    if (response.status === 202) {
      throw new StreamError("http", 202, {
        serverMessage: "服务端仍返回已退役的 202 排队受理，请升级后端后再试",
      });
    }

    await pumpSseBody(
      response,
      conversationId,
      turnCommit
        ? (event) => {
            if (event.type === "turn_saved") turnCommit.committed = true;
            dispatchFoldedSseEvent(event, {
              conversationId,
              source: "server",
            });
          }
        : undefined,
    );

    if (getRuntime(conversationId).isGenerating) {
      throw new StreamError("network");
    }
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    if (err instanceof StreamError) throw err;
    throw new StreamError("network");
  } finally {
    flushPendingContent(conversationId);
    flushPendingFrames(conversationId);
    releasePrimaryStream(conversationId, primaryToken);
    releaseLocalStream();
  }
}

/** 发送给后端的附件载荷（含提取出的正文 / 引用即驻留元数据）。 */
export interface OutgoingAttachment {
  name: string;
  path: string;
  text: string;
  truncated: boolean;
  kind?: "file" | "dir" | "conversation";
  conversation_id?: string;
  /** 二进制驻留：无 UTF-8 正文。 */
  binary?: boolean;
  /** 客户端已写入工作区时的相对路径（``attachments/…``）。 */
  workspace_path?: string;
}

/** `@Agent` 点名（与 attachments 并列；不扩展 MessageAttachment.kind）。 */
export interface OutgoingAgentMention {
  agent_id: string;
  role: string;
}

export interface StreamConversationOptions {
  conversationId: string;
  content: string;
  attachments?: OutgoingAttachment[];
  agentMentions?: OutgoingAgentMention[];
  /** 必填分流（缺 → 服务端 422）。空闲开跑客户端仍带 ``steer``。 */
  delivery: "steer" | "queue";
  signal?: AbortSignal;
  /** 本发泵到 `turn_saved` 时置 `committed`。Class B 回滚读这个事实，不嗅消息 id。 */
  turnCommit?: TurnCommitReport;
}

/** Send a user message and consume the SSE response stream (发送即有流).
 *
 * In-flight 时流上先到 ``turn_queued``（EPHEMERAL，dispatch 侧呈现「已排队」），
 * drain 后同连接续流整回合；`delivery=steer` 插话为 `user_interjection` 短确认流。 */
export async function streamConversation({
  conversationId,
  content,
  attachments,
  agentMentions,
  delivery,
  signal,
  turnCommit,
}: StreamConversationOptions): Promise<void> {
  const payload: Record<string, unknown> = { content, delivery };
  if (attachments && attachments.length > 0) payload.attachments = attachments;
  if (agentMentions && agentMentions.length > 0) {
    payload.agent_mentions = agentMentions;
  }
  await runMessageStream(
    `/v1/conversations/${conversationId}/messages`,
    JSON.stringify(payload),
    conversationId,
    signal,
    turnCommit,
  );
}

export interface RegenerateConversationOptions {
  conversationId: string;
  messageId: string;
  content?: string;
  signal?: AbortSignal;
}

export async function regenerateConversation({
  conversationId,
  messageId,
  content,
  signal,
}: RegenerateConversationOptions): Promise<void> {
  const body = JSON.stringify(content !== undefined ? { content } : {});
  await runMessageStream(
    `/v1/conversations/${conversationId}/messages/${messageId}/regenerate`,
    body,
    conversationId,
    signal,
  );
}

export interface ResumeConversationOptions {
  conversationId: string;
  messageId: string;
  decision: PlanReviewUserDecision;
  note: string;
  selected?: string[];
  /** team_preview（delegate）continue 修正；缺省 / 空 = 全员开工。 */
  excluded_run_ids?: string[];
  write_capability_overrides?: Array<{
    run_id: string;
    capability: "text_only";
  }>;
  /** 人盖 CEO 的 per-run 模型；空/缺 = 不改。 */
  model_overrides?: Record<
    string,
    { model: string; origin?: "platform" | "byok"; provider_id?: string }
  >;
  /** Structured website style pick (s0/s1/…). */
  signal?: AbortSignal;
}

export async function resumeConversation({
  conversationId,
  messageId,
  decision,
  note,
  selected = [],
  excluded_run_ids,
  write_capability_overrides,
  model_overrides,
  signal,
}: ResumeConversationOptions): Promise<void> {
  const body = JSON.stringify({
    decision,
    note,
    selected,
    ...(excluded_run_ids && excluded_run_ids.length > 0
      ? { excluded_run_ids }
      : {}),
    ...(write_capability_overrides && write_capability_overrides.length > 0
      ? { write_capability_overrides }
      : {}),
    ...(model_overrides && Object.keys(model_overrides).length > 0
      ? { model_overrides }
      : {}),
  });
  await runMessageStream(
    `/v1/conversations/${conversationId}/messages/${messageId}/resume`,
    body,
    conversationId,
    signal,
  );
}

export interface ContinueConversationOptions {
  conversationId: string;
  messageId: string;
  signal?: AbortSignal;
}

/** Same-turn continue after attested `outcome=paused` (empty body, SSE). */
export async function continueConversation({
  conversationId,
  messageId,
  signal,
}: ContinueConversationOptions): Promise<void> {
  await runMessageStream(
    `/v1/conversations/${conversationId}/messages/${messageId}/continue`,
    "{}",
    conversationId,
    signal,
  );
}

export interface ResolveStageCardOptions {
  conversationId: string;
  stageCardId: string;
  decision: "start_debate" | "research_first";
  note?: string;
  motionOverride?: string | null;
  signal?: AbortSignal;
}

/** 批 B：推进卡 resolve → SSE 新回合（机制直起辩论或回灌调研）。 */
export async function resolveStageCardConversation({
  conversationId,
  stageCardId,
  decision,
  note = "",
  motionOverride = null,
  signal,
}: ResolveStageCardOptions): Promise<void> {
  const body = JSON.stringify({
    kind: "stage_card",
    decision,
    note,
    motion_override: motionOverride,
  });
  await runMessageStream(
    `/v1/conversations/${conversationId}/interactions/${stageCardId}`,
    body,
    conversationId,
    signal,
  );
}

// Re-export SSE dispatch surface (shared by cloud + sidecar paths).
export {
  dispatchSSEEvent,
  flushPendingContent,
  flushPendingFrames,
} from "./sse/dispatch";
