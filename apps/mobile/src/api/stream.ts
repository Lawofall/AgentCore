import { apiUrl, authHeader, fetchWithAuthRefresh } from "@/api/client";
import type { MessageAttachment } from "@/lib/attachments";
import { clientHeaders } from "@/lib/clientBuildInfo";
import { StreamHttpError } from "@/lib/errors";
import type { RecoveryMomentContext } from "@/lib/recoveryMoment";
// SSE transport for the mobile client (前端技术与架构 §七).
//
// The backend streams a turn as a POST returning text/event-stream (api/sse.py):
// frames are `event: <type>\ndata: {type,timestamp,payload}\n\n`, with `: ping`
// heartbeat comments. Because it's a fetch-streamed POST (not EventSource), the bearer
// header rides the request directly — the key reason bearer + SSE works on mobile.
//
// This layer is PURE TRANSPORT: it parses each `data:` frame into a typed SSEEvent and
// hands it to `onEvent`. All interpretation lives in the conformance-checked fold
// (src/protocol/fold.ts) — never re-fold here (cross-platform-frontend.mdc §四).
//
// 执行与请求解耦 (C1 · slice 1a/1b): a client disconnect no longer cancels a server
// turn — it runs detached and persists. So there are three SSE entry points that all
// fold through the SAME shape: `streamMessage` (fresh send), `resumeStream` (continue a
// durably paused turn), and `followConversation` (对话级订阅：park on the conversation
// itself — it both rejoins a still-live run and picks up whatever runs next, 云对话多端
// 同权 B2；回合级 attach 已被它取代). An explicit 停止 is a separate JSON call
// (api/turn.ts).
import type { CheckpointDecision, SSEEvent } from "@agentcore/contract-types";

/** Build a {@link StreamHttpError} from a non-OK response. A refused turn (e.g.
 *  402 LLM_KEY_REQUIRED / 429 quota) arrives as plain JSON
 *  `{error:{code,message,context}}`, not an SSE stream — pull those out so ChatPage
 *  can offer「去配置」and render the refusal's recovery moment in the user's own zone. */
async function streamErrorFromResponse(
  response: Response,
): Promise<StreamHttpError> {
  let code: string | undefined;
  let serverMessage: string | undefined;
  let context: RecoveryMomentContext | undefined;
  try {
    const body = (await response.json()) as {
      error?: {
        code?: string;
        message?: string;
        context?: RecoveryMomentContext;
      };
    };
    code = body.error?.code;
    serverMessage = body.error?.message;
    context = body.error?.context;
  } catch {
    /* non-JSON body — keep status-only phrasing */
  }
  return new StreamHttpError(response.status, code, serverMessage, context);
}

/** Raised when the SSE body goes silent too long (dead socket / proxy drop). */
export class StreamNetworkError extends Error {
  constructor() {
    super("network");
    this.name = "StreamNetworkError";
  }
}

/** Max silence while the body is open. The backend heart-beats every ~15s during a
 *  thinking turn, so a live connection always delivers bytes; total silence means the
 *  socket is dead — cancel and surface a retriable error (mirrors desktop streamConversation). */
const IDLE_TIMEOUT_MS = 60_000;

/** SSE comment after attach journal replay (+ hot re-hang); mirrors server
 * ``sse._ATTACH_CAUGHT_UP`` / desktop ``ATTACH_CAUGHT_UP_COMMENT``. */
export const ATTACH_CAUGHT_UP_COMMENT = "attach-caught-up";

/** Latest journal seq per conversation (SSE ``id:`` → ``Last-Event-ID`` resume). */
const lastEventIds = new Map<string, string>();

/** Read an SSE response body to completion, delivering each parsed `data:` frame to
 *  `onEvent`. `event:` lines are ignored (the data JSON already carries the type).
 *  Comment frames (``: ping`` / ``: attach-caught-up``) go to ``onComment`` when set.
 *  Throws if the response has no readable body.
 *
 *  Applies the idle stall watchdog: an *idle* timeout, never a total-duration cap — a
 *  long turn that keeps streaming (or just heart-beating) is never cut off. */
async function pumpSSE(
  response: Response,
  onEvent: (event: SSEEvent) => void,
  conversationId?: string,
  onComment?: (comment: string) => void,
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("无响应流");

  const decoder = new TextDecoder();
  let buffer = "";
  let frameId: string | null = null;

  const readChunk = (): ReturnType<typeof reader.read> =>
    new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        void reader.cancel().catch(() => {});
        reject(new StreamNetworkError());
      }, IDLE_TIMEOUT_MS);
      reader.read().then(
        (r) => {
          clearTimeout(timer);
          resolve(r);
        },
        (e) => {
          clearTimeout(timer);
          reject(e);
        },
      );
    });

  while (true) {
    const { done, value } = await readChunk();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; keep the trailing partial in buffer.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      frameId = null;
      for (const line of frame.split("\n")) {
        if (line.startsWith(":")) {
          onComment?.(line.slice(1).trim());
          continue;
        }
        if (line.startsWith("id:")) {
          const id = line.slice(3).trim();
          if (id && conversationId) {
            frameId = id;
            lastEventIds.set(conversationId, id);
          }
          continue;
        }
        if (!line.startsWith("data:")) continue;
        try {
          if (frameId && conversationId)
            lastEventIds.set(conversationId, frameId);
          onEvent(JSON.parse(line.slice(5).trim()) as SSEEvent);
        } catch {
          // Skip a malformed/partial frame; the next read completes it.
        }
      }
    }
  }
}

/** Run a fetch with the shared 401 policy (refresh once, replay; still-401 clears
 *  tokens). The SSE channels read the body themselves, so they can't ride apiFetch. */
async function sseFetch(doFetch: () => Promise<Response>): Promise<Response> {
  return fetchWithAuthRefresh(doFetch);
}

/**
 * Stream a freshly-sent user message, delivering each parsed SSE event to `onEvent`.
 * Throws on a transport failure (non-2xx / no body) or when the passed `signal` aborts
 * (the user's 停止); backend `error` events arrive as normal events for the fold.
 *
 * Since 执行与请求解耦 (slice 1a) a dropped connection no longer kills the turn — it runs
 * detached — so a mid-stream throw means "rejoin it" (attachStream), not "resend".
 *
 * `attachments` carry extracted file text and/or resident binary metadata alongside the
 * prompt (composer 附件); omitted from the body when empty so a plain turn keeps the exact
 * prior shape.
 *
 * ``delivery`` 必填（同对话再发）：空闲开跑仍带 ``steer``；缺 → 422。
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  attachments?: MessageAttachment[],
  delivery: "steer" | "queue" = "steer",
  agentMentions?: { agent_id: string; role: string }[],
): Promise<void> {
  const path = `/v1/conversations/${conversationId}/messages`;
  const payload: Record<string, unknown> = { content, delivery };
  if (attachments && attachments.length > 0) payload.attachments = attachments;
  if (agentMentions && agentMentions.length > 0) {
    payload.agent_mentions = agentMentions;
  }
  const response = await sseFetch(() =>
    fetch(apiUrl(path), {
      method: "POST",
      headers: {
        ...clientHeaders(),
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...authHeader(),
      },
      body: JSON.stringify(payload),
      signal,
    }),
  );
  if (!response.ok) throw await streamErrorFromResponse(response);
  await pumpSSE(response, onEvent, conversationId);
}

/**
 * 订阅一个**对话**（而不是某个回合）并跟播它此后的每个回合（云对话多端同权 B2）。
 *
 * 端点同回合级 attach，多带 `follow=true`：空闲不再 204，而是保持连接送心跳，
 * 之后每个新回合（另一端发送 / 队列出队 / 冷 resume 唤醒 / 推进卡）都在同一条流上重放 +
 * 跟播。这正是「停在空闲对话上的第二台设备自动出现另一台刚起的回合」所缺的那一环——回合级
 * attach 拿到 204 之后就再也无从得知发生过任何事。
 *
 * 帧序：`[回合重放…] : attach-caught-up [实时帧…]`，回合收口后回到 `: ping` 等下一个回合。
 * 首个回合段照 attach 语义缓冲到边界注释再整段送出（已完工的队员不会在重开时重新动画）；
 * 之后逐条送出——回合切分由调用方按 `message_start` 的 `message_id` 判定，因为新回合的重放段
 * 先于它自己的边界注释到达，缓冲无从提前进入。
 *
 * `onIdle` = 旧 204 的对话级等价物：**任何边界注释之前**先收到心跳。重放段与它的边界注释之间
 * 不会插心跳，所以这是结构判据、不含时序假设（慢重放也不会误报空闲）。至多回调一次。
 */
export async function followConversation(
  conversationId: string,
  onEvent: (event: SSEEvent) => void,
  onIdle: () => void,
  signal?: AbortSignal,
): Promise<void> {
  const path = `/v1/conversations/${conversationId}/stream?follow=true`;
  const response = await sseFetch(() =>
    fetch(apiUrl(path), {
      method: "GET",
      headers: {
        ...clientHeaders(),
        Accept: "text/event-stream",
        // 同 attach：连上时若已有回合在跑，走 journal 全量重放（值仅供观测）。
        "Last-Event-ID": lastEventIds.get(conversationId) ?? "0",
        ...authHeader(),
      },
      signal,
    }),
  );
  // 老后端不认 `follow`，空闲仍按回合级语义 204 —— 降级成「连上来就空闲」而不是报错：
  // 拿不到自动跟播，但对话本身照常可用。
  if (response.status === 204) {
    onIdle();
    return;
  }
  if (!response.ok) throw await streamErrorFromResponse(response);

  const catchUp: SSEEvent[] = [];
  let catchingUp = true;
  await pumpSSE(
    response,
    (event) => {
      if (catchingUp) {
        catchUp.push(event);
        return;
      }
      onEvent(event);
    },
    conversationId,
    (comment) => {
      if (!catchingUp) return; // 首段之后：边界注释与心跳都无需处理
      if (comment === ATTACH_CAUGHT_UP_COMMENT) {
        catchingUp = false;
        for (const e of catchUp) onEvent(e);
        catchUp.length = 0;
        return;
      }
      // 心跳且首个回合段一帧未到 → 连上来时没有回合在跑。
      if (catchUp.length > 0) return;
      catchingUp = false;
      onIdle();
    },
  );
  // 服务端关流时首段还没等到边界（老服务端 / 收口竞态）——照 attach 兜底刷出。
  if (catchingUp && catchUp.length > 0) {
    for (const e of catchUp) onEvent(e);
  }
}

/** Delegate team_preview 开工卡修正：排除岗 + 单向收紧写盘 + 人盖模型（定案 §3.3）. */
export interface WriteCapabilityOverride {
  run_id: string;
  capability: "text_only";
}

/** Per-run model cover（人盖 CEO）；键 = run_id。 */
export type TeamPreviewModelOverride = {
  model: string;
  origin?: "platform" | "byok";
  provider_id?: string;
};

export interface TeamPreviewAmendments {
  /** Delegate only; empty/omit = keep all workers. */
  excluded_run_ids?: string[];
  /** Delegate only; empty/omit = no write tighten. */
  write_capability_overrides?: WriteCapabilityOverride[];
  /** 仅非空时附带；空/缺 = 不改节点模型。辩论/队员共用。 */
  model_overrides?: Record<string, TeamPreviewModelOverride>;
}

/** The user's settlement of a durably-paused turn (mirrors backend ResumeTurnRequest).
 *  `note` steers an `adjust`; `selected` carries ask_user picks (ignored for plan_review).
 *  `excluded_run_ids` / `write_capability_overrides` / `model_overrides` only on
 *  delegate team_preview continue. */
export interface ResumeTurnBody {
  decision: CheckpointDecision;
  note: string;
  selected: string[];
  excluded_run_ids?: string[];
  write_capability_overrides?: WriteCapabilityOverride[];
  model_overrides?: Record<string, TeamPreviewModelOverride>;
}

/**
 * Continue a durably-paused turn via SSE (结构化挂起 2b `POST .../resume`).
 *
 * The turn paused at a plan_review / ask_user checkpoint and lost its live stream
 * (disconnect / restart); only its persisted frame survived. On success the backend
 * claims the frame and drives the rest of the turn on a fresh SSE, folded through
 * the same path as a send.
 *
 * Busy slot (parallel live / host wrap-up): same connection first emits EPHEMERAL
 * ``resume_deferred`` ``{ message_id, conversation_id, busy_reason }`` then waits
 * and continues — not a 409. Throws on transport / claim failure.
 */
export async function resumeStream(
  conversationId: string,
  messageId: string,
  body: ResumeTurnBody,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const path = `/v1/conversations/${conversationId}/messages/${messageId}/resume`;
  const response = await sseFetch(() =>
    fetch(apiUrl(path), {
      method: "POST",
      headers: {
        ...clientHeaders(),
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...authHeader(),
      },
      body: JSON.stringify(body),
      signal,
    }),
  );
  if (!response.ok) throw await streamErrorFromResponse(response);
  await pumpSSE(response, onEvent, conversationId);
}

/**
 * Continue a CEO rate-limit pause (`outcome=paused`) via SSE
 * (`POST .../messages/{id}/continue`, empty body). Not the checkpoint `/resume`.
 */
export async function continueStream(
  conversationId: string,
  messageId: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const path = `/v1/conversations/${conversationId}/messages/${messageId}/continue`;
  const response = await sseFetch(() =>
    fetch(apiUrl(path), {
      method: "POST",
      headers: {
        ...clientHeaders(),
        Accept: "text/event-stream",
        ...authHeader(),
      },
      signal,
    }),
  );
  if (!response.ok) throw await streamErrorFromResponse(response);
  await pumpSSE(response, onEvent, conversationId);
}

/**
 * Re-run from a persisted user message (regenerate endpoint · P4 interrupted retry).
 * Same SSE shape as ``streamMessage`` — fold through the caller's ``onEvent``.
 */
export async function regenerateStream(
  conversationId: string,
  userMessageId: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const path = `/v1/conversations/${conversationId}/messages/${userMessageId}/regenerate`;
  const response = await sseFetch(() =>
    fetch(apiUrl(path), {
      method: "POST",
      headers: {
        ...clientHeaders(),
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...authHeader(),
      },
      body: "{}",
      signal,
    }),
  );
  if (!response.ok) throw await streamErrorFromResponse(response);
  await pumpSSE(response, onEvent, conversationId);
}

/** @internal Test hook — production `pumpSSE` with the idle stall watchdog. */
export const pumpSSEForTests = pumpSSE;
