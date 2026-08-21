import type { MessageDelivery } from "@/lib/composerDelivery";
import { streamErrorFromResponse } from "@/lib/errors";
import { logEvent } from "@/lib/log";
import { notifyError } from "@/lib/toast";
import {
  ApiError,
  BASE_URL,
  captureCsrf,
  getCsrfHeaders,
  isReplayableCsrfRejection,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";
import { dispatchSSEEvent } from "@/services/sse/dispatch";
import {
  type OutgoingAgentMention,
  type OutgoingAttachment,
  pumpSseBody,
} from "@/services/streamConversation";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import type {
  SSEEvent,
  TurnQueuedPayload,
  UserInterjectionPayload,
} from "@/types/events";
import {
  type ActiveSidecarTurn,
  getActiveSidecarTarget,
  getLastSidecarTarget,
} from "../sidecarRouting";
import {
  beginLocalConversationStream,
  claimPrimaryStream,
  isPrimaryStreamIdle,
  onPrimaryStreamIdle,
  releasePrimaryStream,
  waitForPrimaryStreamIdle,
} from "./streamOwnership";

export type MidFlightSendResult =
  | { kind: "received"; interjectionId: string }
  | { kind: "queued"; position: number; queueDepth: number; queueId: string }
  | { kind: "blocked"; code?: string }
  | { kind: "error" };

type DeliverMode = "open" | "buffering" | "live" | "aborted";

function upsertQueuedAck(
  conversationId: string,
  content: string,
  attachments: OutgoingAttachment[] | undefined,
  agentMentions: OutgoingAgentMention[] | undefined,
  ack: {
    queueId: string;
    position: number;
    queueDepth: number;
    degradedFrom?: "steer";
    /** 用户气泡 id（取消删泡）；不是助手行 id。 */
    messageId?: string;
  },
): MidFlightSendResult {
  useQueuedTurnsStore.getState().upsert({
    queueId: ack.queueId,
    conversationId,
    content,
    attachments:
      attachments && attachments.length > 0
        ? attachments.map((a) => ({ ...a }))
        : undefined,
    agentMentions:
      agentMentions && agentMentions.length > 0
        ? agentMentions.map((a) => ({ ...a }))
        : undefined,
    position: ack.position,
    queueDepth: ack.queueDepth,
    degradedFrom: ack.degradedFrom,
    ...(ack.messageId ? { messageId: ack.messageId } : {}),
  });
  return {
    kind: "queued",
    position: ack.position,
    queueDepth: ack.queueDepth,
    queueId: ack.queueId,
  };
}

/**
 * 本会话 sidecar live：走 RPC，禁止 POST 云 ``/messages``；事件仍走现有 turn/event 泵。
 * 无进程 / RPC 失败 → error，不得当成发送成功、不得回落云。
 */
async function deliverViaSidecar(
  conversationId: string,
  target: ActiveSidecarTurn,
  content: string,
  attachments: OutgoingAttachment[] | undefined,
  delivery: MessageDelivery,
  agentMentions: OutgoingAgentMention[] | undefined,
): Promise<MidFlightSendResult> {
  try {
    const userMessageId = crypto.randomUUID();
    const messageId = crypto.randomUUID();
    const traceId = crypto.randomUUID().replace(/-/g, "");
    const ack = await window.sidecarApi.deliverMessage({
      rootId: target.rootId,
      subpath: target.subpath,
      conversationId,
      content,
      delivery,
      userMessageId,
      messageId,
      traceId,
      ...(attachments && attachments.length > 0 ? { attachments } : {}),
      ...(agentMentions && agentMentions.length > 0 ? { agentMentions } : {}),
    });
    if (ack.status === "received") {
      return { kind: "received", interjectionId: ack.interjectionId };
    }
    if (ack.status === "queued") {
      return upsertQueuedAck(
        conversationId,
        content,
        attachments,
        agentMentions,
        {
          queueId: ack.queueId,
          position: ack.position,
          queueDepth: ack.queueDepth,
          degradedFrom: ack.degradedFrom,
          messageId: userMessageId,
        },
      );
    }
    notifyError(new Error("请先处理待确认事项"), "请先处理待确认事项");
    return { kind: "blocked", code: ack.code };
  } catch (err) {
    notifyError(err, "发送失败");
    return { kind: "error" };
  }
}

/** 本机引擎仍占着这通对话：活回合，或队还没排完（上一回合刚收口、FIFO 尚未 claim）。 */
function resolveSidecarInFlightTarget(
  conversationId: string,
): ActiveSidecarTurn | { rootId: string; subpath: string } | null {
  const live = getActiveSidecarTarget(conversationId);
  if (live) return live;
  if (useQueuedTurnsStore.getState().list(conversationId).length === 0) {
    return null;
  }
  return getLastSidecarTarget(conversationId);
}

/**
 * POST a user message while a turn is already streaming（发送即有流）.
 *
 * ``delivery=steer``：
 * - 经典 / 协调 → ``user_interjection``（ack = ``status === "received"``；主时间线
 *   InterjectionTimeline 投影；经典终态多为 ``injected``，协调经 ``injected`` 再到
 *   ``addressed`` / ``queued`` / ``failed``）
 * - 不可注入 → ``user_interjection(queued)`` + ``turn_queued.degraded_from=steer``
 * ``delivery=queue``（强制）→ ``turn_queued`` 只 upsert QueuedTurnsBar；
 * ``turn_queue_started`` 出队开跑再插主时间线用户泡；后续帧缓冲至 turn1 主路释放再续流。
 *
 * ack（queued / received）后 Promise 即 resolve，调用方可清 composer；
 * SSE 泵与 buffering/drain 在后台续跑。
 * POST 在调用时刻发出（D9 FIFO 位次已占）；缓冲只推迟客户端 fold。
 * Stop/abort **不** cancel 服务端队列（可见条仍可按项取消）。
 */
export async function sendMidFlightMessage(
  conversationId: string,
  content: string,
  attachments: OutgoingAttachment[] | undefined,
  delivery: MessageDelivery,
  agentMentions?: OutgoingAgentMention[],
): Promise<MidFlightSendResult> {
  const sidecarTarget = resolveSidecarInFlightTarget(conversationId);
  if (sidecarTarget) {
    return deliverViaSidecar(
      conversationId,
      sidecarTarget,
      content,
      attachments,
      delivery,
      agentMentions,
    );
  }

  const body: Record<string, unknown> = { content, delivery };
  if (attachments && attachments.length > 0) body.attachments = attachments;
  if (agentMentions && agentMentions.length > 0) {
    body.agent_mentions = agentMentions;
  }

  const ac = new AbortController();
  let abortRegistered = false;
  let result: MidFlightSendResult = { kind: "error" };
  /** 闭包内可变；对象字段避免 TS 把字面量 mode 收窄成永 false。 */
  const gate = { mode: "open" as DeliverMode };
  const buffer: SSEEvent[] = [];
  let queuedPrimaryToken: string | null = null;
  let unsubIdle: () => void = () => {};

  // 与 turn1 AbortSignal 联动：断连丢缓冲，**不** cancel 服务端队列（D9）。
  // 注意：stopGeneration 诚实停止不 abort AbortSignal，排队连接可继续等 drain。
  const parentAbort = getRuntime(conversationId).abort;
  const onParentAbort = (): void => ac.abort();
  parentAbort?.signal.addEventListener("abort", onParentAbort);

  const registerAbort = (): void => {
    if (abortRegistered) return;
    useConversationStore.getState().setAbort(ac, conversationId);
    abortRegistered = true;
  };

  /** 出队插泡由 messageStream 读 ``turn_queue_started`` 帧；此处只折。 */
  const dispatchOne = (event: SSEEvent): void => {
    dispatchSSEEvent(event, { conversationId, source: "server" });
  };

  const discardBufferIfAborted = (): boolean => {
    if (!ac.signal.aborted && gate.mode !== "aborted") return false;
    gate.mode = "aborted";
    buffer.length = 0;
    unsubIdle();
    unsubIdle = () => {};
    return true;
  };

  const flushBufferAndGoLive = (): void => {
    // release 与 abort 同刻：waiter 同步唤 flush 须先于 fold 挡下（泵 Abort 分支来不及）。
    if (discardBufferIfAborted()) return;
    if (gate.mode !== "buffering") return;
    gate.mode = "live";
    unsubIdle();
    unsubIdle = () => {};
    if (!queuedPrimaryToken) {
      queuedPrimaryToken = claimPrimaryStream(conversationId);
    }
    const pending = buffer.splice(0);
    for (const ev of pending) dispatchOne(ev);
  };

  const armIdleFlush = (): void => {
    unsubIdle();
    if (isPrimaryStreamIdle(conversationId)) {
      flushBufferAndGoLive();
      return;
    }
    unsubIdle = onPrimaryStreamIdle(conversationId, () => {
      if (discardBufferIfAborted()) return;
      if (gate.mode === "buffering") flushBufferAndGoLive();
    });
  };

  const doFetch = async (signal: AbortSignal): Promise<Response> => {
    const response = await fetch(
      `${BASE_URL}/v1/conversations/${conversationId}/messages`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...getCsrfHeaders("POST"),
        },
        body: JSON.stringify(body),
        signal,
      },
    );
    captureCsrf(response); // token rides every response, streams included
    return response;
  };

  // 对话级订阅让位（B2）：本条 POST 一发出，服务端就在同一会话上排出了新回合——
  // 若 follow 流还开着，drain 后同一回合会被折两次。primary claim 要等 drain 才拿
  // （提前拿自锁），故这里单独占本端连接闸，由 runPump 的 finally 释放。
  const releaseLocalStream = beginLocalConversationStream(conversationId);
  let pumpOwnsLocalStream = false;

  try {
    let response = await doFetch(ac.signal);
    // 两条自愈共用一次重发预算：服务端持续拒绝也只多发一次，绝不叠成三次。
    let replayed = false;
    if (response.status === 401) {
      const outcome = await tryRefresh();
      if (outcome === "renewed") {
        response = await doFetch(ac.signal);
        replayed = true;
      } else if (outcome === "auth_dead") {
        notifyUnauthorized();
        return { kind: "error" };
      } else {
        notifyError(new Error("network"), "发送失败");
        return { kind: "error" };
      }
    }
    // 重发一条会插话 / 起回合的 POST，安全性全由判据本身保证：CSRF 中间件在任何 handler
    // 之前就拒了，服务端从未受理这条消息（不会双开回合、不会重复插话），且这次拒绝回发了
    // 新令牌——`doFetch` 已吸收，且它每次调用重算 header，重发才带得上。没回发令牌的 403
    // 是服务端刻意不重新武装（令牌属于别的会话），必须原样失败。论证见
    // {@link isReplayableCsrfRejection}。
    if (!replayed && response.status === 403) {
      const refusal = new ApiError(
        response.status,
        await response.clone().text(),
        response.headers,
      );
      if (isReplayableCsrfRejection(response, refusal)) {
        logEvent("info", "auth.csrf_replay", {
          conversation_id: conversationId,
          via: "mid_flight",
        });
        replayed = true;
        response = await doFetch(ac.signal);
      }
    }
    if (response.status === 409) {
      let code: string | undefined;
      try {
        const errBody = (await response.json()) as {
          error?: { code?: string; message?: string };
          detail?: { code?: string; message?: string } | string;
        };
        code =
          errBody.error?.code ??
          (typeof errBody.detail === "object"
            ? errBody.detail?.code
            : undefined);
        notifyError(
          new Error(errBody.error?.message ?? "请先处理待确认事项"),
          "请先处理待确认事项",
        );
      } catch {
        notifyError(new Error("请先处理待确认事项"), "请先处理待确认事项");
      }
      return { kind: "blocked", code };
    }
    if (response.status === 202) {
      notifyError(new Error("服务端仍返回已退役的 202 排队受理"), "发送失败");
      return { kind: "error" };
    }
    if (!response.ok) {
      // 解析 `{error:{code,message}}`，让同一个后端拒绝（CSRF 403、限流 …）在插话
      // 链路上和聊天主链路读到同一句话，而不是一句「HTTP 403」。
      notifyError(await streamErrorFromResponse(response), "发送失败");
      return { kind: "error" };
    }

    // ack 后即可 resolve；泵 / 缓冲 / drain 后台续跑。
    let settleAck: ((r: MidFlightSendResult) => void) | null = null;
    let ackSettled = false;
    const ackPromise = new Promise<MidFlightSendResult>((resolve) => {
      settleAck = resolve;
    });
    const finishAck = (r: MidFlightSendResult): void => {
      if (ackSettled) return;
      ackSettled = true;
      settleAck?.(r);
    };

    const runPump = async (): Promise<void> => {
      try {
        await pumpSseBody(response, conversationId, (event: SSEEvent) => {
          if (gate.mode === "aborted" || ac.signal.aborted) return;

          if (event.type === "user_interjection") {
            // 经典/协调插话：即时 dispatch，不缓冲、不占主路门。
            // ack 仅 ``status === "received"``（与协调同形）；后续 injected/终态仍投影。
            gate.mode = "live";
            const p = event.payload as UserInterjectionPayload;
            const iid = (p.interjection_id || "").trim();
            dispatchSSEEvent(event, { conversationId, source: "server" });
            if (iid && p.status === "received") {
              result = { kind: "received", interjectionId: iid };
              finishAck(result);
            }
            return;
          }

          if (event.type === "turn_queued") {
            const p = event.payload as TurnQueuedPayload;
            const position = p.position ?? 1;
            const queueDepth = p.queue_depth ?? 1;
            const queueId = p.queue_id;
            result = upsertQueuedAck(
              conversationId,
              content,
              attachments,
              agentMentions,
              {
                queueId,
                position,
                queueDepth,
                degradedFrom: p.degraded_from === "steer" ? "steer" : undefined,
              },
            );
            registerAbort();
            // toast / degraded 由 dispatch → messageStream 呈现。
            dispatchSSEEvent(event, { conversationId, source: "server" });
            finishAck(result);
            gate.mode = "buffering";
            armIdleFlush();
            return;
          }

          if (gate.mode === "buffering") {
            buffer.push(event);
            if (isPrimaryStreamIdle(conversationId)) flushBufferAndGoLive();
            return;
          }

          dispatchOne(event);
        });

        // 泵正常结束但仍 buffering：主路空则放行；若已 abort（mock 流 close 未抛）则丢缓冲。
        if (ac.signal.aborted) {
          gate.mode = "aborted";
          buffer.length = 0;
          return;
        }
        if (gate.mode === "buffering") {
          if (!isPrimaryStreamIdle(conversationId)) {
            await waitForPrimaryStreamIdle(conversationId);
          }
          if (!ac.signal.aborted) flushBufferAndGoLive();
          else {
            gate.mode = "aborted";
            buffer.length = 0;
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // 排队等待中断连：丢未放行缓冲；**保留**排队条（Stop ≠ 取消排队）。
          gate.mode = "aborted";
          buffer.length = 0;
          return;
        }
        notifyError(err, "发送失败");
        result = { kind: "error" };
      } finally {
        parentAbort?.signal.removeEventListener("abort", onParentAbort);
        unsubIdle();
        if (queuedPrimaryToken) {
          releasePrimaryStream(conversationId, queuedPrimaryToken);
          queuedPrimaryToken = null;
        }
        if (abortRegistered && getRuntime(conversationId).abort === ac) {
          useConversationStore.getState().setAbort(null, conversationId);
        }
        releaseLocalStream();
        finishAck(result);
      }
    };

    void runPump();
    pumpOwnsLocalStream = true;
    return ackPromise;
  } catch (err) {
    parentAbort?.signal.removeEventListener("abort", onParentAbort);
    if (err instanceof DOMException && err.name === "AbortError") {
      return result;
    }
    notifyError(err, "发送失败");
    return { kind: "error" };
  } finally {
    // 早退分支（401/409/202/!ok/抛错）没有泵可释放。
    if (!pumpOwnsLocalStream) releaseLocalStream();
  }
}
