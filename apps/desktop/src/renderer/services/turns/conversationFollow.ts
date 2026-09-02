/**
 * 对话级订阅（云对话多端同权 B2 · P0-b · 验收 4）。
 *
 * 回合级 attach 绑的是**回合**：空闲对话拿 204，此后另一端发送 / 队列 drain /
 * 冷 resume 唤醒 / stage_card 起的每个新回合都是新 sink，拿过 204 的这端零信号。
 * 这里改订**对话**：``GET …/stream?follow=true`` 空闲时只收心跳保持连接，之后每个
 * 新回合在同一条流上自动重放 + 跟播，桌面停在空闲对话上也能自动出现新回合。
 *
 * 三条边界：
 *
 * - **不与本端自有连接同折一个回合**。本端 POST 回合流 / 回合级 attach / midFlight
 *   排队连接一开，这条订阅**静音**（连接不断、帧不折、游标不推进），闲下来同一条
 *   流接着收——直到下一段 ``full_replay``（别人开的新回合）才再折。禁止 abort 重连
 *   再整段重放：那会把本机刚折完的回合闪一次。互斥闸见 ``streamOwnership`` 的
 *   ``beginLocalConversationStream``。
 * - **空闲不是「生成中」**。真空闲时本模块一个 store 都不写：不开气泡、不置
 *   ``isGenerating``、不占 abort 槽，也不因掉线弹横幅（后台观察者，静默退避重连）。
 * - **切走立刻停**。对话级订阅只服务当前揭开的窗口：切到别的会话 / 草稿 / 离页立刻
 *   ``stop``，不 abort 本端 POST / sidecar 泵。follow-only 撑着的 ``isGenerating``
 *   随之落下（所有权只认 ``hasLocalConversationStream``）。
 *
 * 两个正交的决定，各有各的依据：
 *
 * - **分段与清不清**由服务端说了算。重放段段首的 ``message_start`` 带 ``full_replay``，即
 *   「这是本回合的全量重放」——照做重置再折。本端不再拿段首 id 去比屏幕上的气泡猜这一轮
 *   是不是自己的（猜错就把正文折两遍）。
 * - **补不补历史**由本地事实说了算。SSE 只带回合事件、**不带用户消息正文**，所以本地没有
 *   这一轮上下文时要先把消息窗拉齐一次，不然屏幕上只会冒出一个没有提问的助手气泡。
 */
import { clientHeaders } from "@/lib/clientBuildInfo";
import { logEvent } from "@/lib/log";
import { bearerAuthHeader, sessionCredentials } from "@/lib/sessionAuth";
import { BASE_URL, captureCsrf, tryRefresh } from "@/services/api";
import { loadLatestWindow } from "@/services/messages";
import {
  ATTACH_CAUGHT_UP_COMMENT,
  dispatchFoldedSseEvent,
  foldAttachSegment,
  peekLastEventId,
  pumpSseBody,
} from "@/services/streamConversation";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import type { MessageStartPayload, SSEEvent } from "@/types/events";
import { reconnectBackoffMs } from "./reconnectBackoff";
import {
  hasLocalConversationStream,
  subscribeLocalConversationStream,
} from "./streamOwnership";

type FollowSlot = {
  conversationId: string;
  /** 终止：不再重连，循环退出。 */
  stopped: boolean;
  /** 本端自有连接占用中 → 让位；闲下来由订阅回调唤醒。 */
  suspended: boolean;
  attempts: number;
  ac: AbortController | null;
  /**
   * 当前这条 SSE 已经打过 ``follow_open``。拆 slot 时据此成对打
   * ``follow_closed``；没建连不打，避免排查包里凭空多一条关闭。
   */
  opened: boolean;
  /**
   * 本端闸亮过：静音期间与放闸后的同回合云投影都不折，直到下一段别人的
   * ``full_replay``（或自回放段的 ``attach-caught-up`` 丢完）。
   */
  ignoreUntilFullReplay: boolean;
  /** 正在丢掉本端刚写完的那一轮云侧 catch-up，不入屏。 */
  drainingSelfReplay: boolean;
  unsubBusy: () => void;
  /** 唤醒当前的等待（退避 sleep / 让位挂起）。 */
  wake: (() => void) | null;
};

const slots = new Map<string, FollowSlot>();

/** 本端自有连接一开就静音订阅，不断 SSE。
 * 帧的丢弃由 ``slot.suspended`` / ``ignoreUntilFullReplay`` 兜住（已解码进微任务
 * 的那一片仍会回调，但不折）。不拆 slot——闲下来同一条连接接着收。 */
function onLocalStreamBusy(slot: FollowSlot, busy: boolean): void {
  slot.suspended = busy;
  if (busy) {
    slot.ignoreUntilFullReplay = true;
    slot.drainingSelfReplay = false;
    if (slot.opened) {
      logEvent("info", "conversation.follow_muted", {
        conversation_id: slot.conversationId,
        reason: "local_stream_handoff",
      });
    }
    return;
  }
  if (slot.opened) {
    logEvent("info", "conversation.follow_unmuted", {
      conversation_id: slot.conversationId,
      reason: "local_stream_handoff",
    });
  }
  slot.wake?.();
}

function wakeSlot(slot: FollowSlot): void {
  slot.wake?.();
}

function sleep(slot: FollowSlot, ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      slot.wake = null;
      resolve();
    }, ms);
    slot.wake = () => {
      clearTimeout(timer);
      slot.wake = null;
      resolve();
    };
  });
}

function waitUntilResumable(slot: FollowSlot): Promise<void> {
  if (slot.stopped || !slot.suspended) return Promise.resolve();
  return new Promise<void>((resolve) => {
    slot.wake = () => {
      slot.wake = null;
      resolve();
    };
  });
}

function stopFollowOwnedGenerating(conversationId: string): void {
  if (hasLocalConversationStream(conversationId)) return;
  if (getRuntime(conversationId).isGenerating) {
    useConversationStore.getState().setGenerating(false, conversationId);
  }
}

function stopSlot(slot: FollowSlot, reason: string): void {
  if (slot.stopped) return;
  slot.stopped = true;
  slot.unsubBusy();
  slot.ac?.abort();
  slot.ac = null;
  wakeSlot(slot);
  if (slots.get(slot.conversationId) === slot) {
    slots.delete(slot.conversationId);
  }
  stopFollowOwnedGenerating(slot.conversationId);
  logEvent("info", "conversation.follow_closed", {
    conversation_id: slot.conversationId,
    reason,
  });
}

/** 段首 ``message_start`` 的 payload（``undefined`` = 这一段没有段首：只带
 * ``resume_settled`` lead / 队列信号 / hot 卡重挂的段）。 */
function segmentStart(segment: SSEEvent[]): MessageStartPayload | undefined {
  const start = segment.find((e) => e.type === "message_start");
  return start ? (start.payload as MessageStartPayload) : undefined;
}

function assistantTurnOnScreen(
  conversationId: string,
  turnId: string | undefined,
): boolean {
  if (!turnId) return false;
  return getRuntime(conversationId).messages.some(
    (m) =>
      m.role === "assistant" &&
      (m.serverMessageId === turnId || m.id === turnId),
  );
}

/**
 * 折这一段之前要不要先把消息窗拉齐？
 *
 * 判据是**本地事实**——本地有没有这一轮的上下文，而不是「这一轮是不是我的」（那只能猜）。
 * SSE 只带回合事件、不带用户消息正文：本地找不到这条回合 id 的助手气泡，就说明这轮是别处
 * 开的、它的提问只在 REST 里，直接折只会冒出一个没有提问的助手气泡。
 *
 * 找的是**整个消息窗里有没有这条回合 id**，不是末条气泡是不是它：末尾挂着一个尚未盖上
 * 服务端 id 的占位泡时，这一轮的上下文照样在屏幕上。
 */
function needsWindowBackfill(
  conversationId: string,
  turnId: string | undefined,
): boolean {
  return !assistantTurnOnScreen(conversationId, turnId) && Boolean(turnId);
}

/**
 * 这帧 ``message_start`` 是重放段的段首（``full_replay``），还是直播帧？
 *
 * 同一条连接上的每个 run 都从自己的重放段开始，段首必带这个标记（服务端会把历史段首改写成
 * 带标记的副本，历史里没有就补一帧合成段首），所以「开新段」这件事只认它，不去猜。
 */
function opensFullReplaySegment(event: SSEEvent): boolean {
  return (event.payload as MessageStartPayload).full_replay === true;
}

async function foldCatchUpSegment(
  slot: FollowSlot,
  segment: SSEEvent[],
): Promise<void> {
  const conversationId = slot.conversationId;
  const start = segmentStart(segment);
  let skipQueuedTurnUserBubble = false;
  if (needsWindowBackfill(conversationId, start?.message_id)) {
    // 别处开的回合：先把消息窗拉齐，用户那条提问只在 REST 里。
    // 不传页级 AbortSignal：补窗属于这条订阅，切走停的是订阅本身。
    try {
      skipQueuedTurnUserBubble =
        (await loadLatestWindow(conversationId, { softRefresh: true })) ===
        true;
    } catch {
      /* best-effort：窗口没拉到也照样跟播；started 帧带 content 时可补插 */
    }
    // 拉窗口期间本端自有连接开张 → 整段交给它重放（同一段首指令），这里折了只会闪一下。
    if (slot.stopped || slot.suspended) return;
  }
  // 清不清由段首说了算——与回合级 attach 同一份判断，两条路不得各自解读。
  // 补窗成功 → 折 started 只清条不插泡（REST 已有用户行）。
  foldAttachSegment(conversationId, segment, { skipQueuedTurnUserBubble });
}

type ConnectionOutcome = "ok" | "retry" | "stop";

async function followFetch(
  conversationId: string,
  signal: AbortSignal,
): Promise<Response> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    ...clientHeaders(),
    ...bearerAuthHeader(),
  };
  // 恒带：``0`` = 本端没有游标（服务端据此回整段），否则报出看到的最后一个 journal seq。
  headers["Last-Event-ID"] = peekLastEventId(conversationId) ?? "0";
  const response = await fetch(
    `${BASE_URL}/v1/conversations/${conversationId}/stream?follow=true`,
    { method: "GET", credentials: sessionCredentials(), headers, signal },
  );
  captureCsrf(response); // 长订阅也带令牌，丢了就等着下一次写请求 403
  return response;
}

/**
 * 一条连接的收帧循环。
 *
 * 分段规则跟着服务端 ``_attach_frames``：每个回合 = 重放段 → ``: attach-caught-up``
 * → 直播段。首段整段缓冲到边界再一次性折（避免已完成的 worker 再演一遍
 * running→completed）；此后每见到一个带 ``full_replay`` 的 ``message_start`` 就重新收拢
 * 缓冲，补完历史再折。
 */
async function pumpFollowBody(
  slot: FollowSlot,
  response: Response,
): Promise<void> {
  const conversationId = slot.conversationId;
  const buffer: SSEEvent[] = [];
  let buffering = true;
  let releasing = false;
  /** 在飞的折（拉窗口是异步的）——断流后要等它落地再让外层重连，否则两条连接会交叉折。 */
  let releasePending: Promise<void> | null = null;

  const releaseBuffer = (): void => {
    if (releasing) return;
    releasing = true;
    releasePending = (async () => {
      try {
        const segment = buffer.splice(0);
        if (segment.length > 0 && !slot.stopped && !slot.suspended) {
          await foldCatchUpSegment(slot, segment);
        }
        // 折的过程中（拉窗口是异步的）继续进的帧属于同一回合的续播——按序直折，
        // 不能再当 catch-up 段走一次 clear-then-fold（那会把刚折进去的抹掉）。
        while (buffer.length > 0 && !slot.stopped && !slot.suspended) {
          const next = buffer.shift();
          if (next) {
            dispatchFoldedSseEvent(next, { conversationId, source: "server" });
          }
        }
      } finally {
        buffer.length = 0;
        releasing = false;
        buffering = false;
      }
    })();
  };

  const openGate = (): void => {
    if (!buffering || releasing) return;
    if (buffer.length === 0) {
      buffering = false; // 没有 catch-up 段可折（空闲连接 / 已折过）
      return;
    }
    releaseBuffer();
  };

  try {
    await pumpSseBody(
      response,
      conversationId,
      (event) => {
        if (slot.stopped) return;
        if (slot.suspended) {
          buffer.length = 0;
          buffering = false;
          return;
        }
        if (slot.drainingSelfReplay) return;
        if (slot.ignoreUntilFullReplay) {
          if (event.type === "message_start" && opensFullReplaySegment(event)) {
            const mid = (event.payload as MessageStartPayload).message_id;
            if (assistantTurnOnScreen(conversationId, mid)) {
              // 本端刚折完的同一轮云回放：丢掉整段，避免 full_replay 把屏幕闪一次。
              slot.drainingSelfReplay = true;
              buffer.length = 0;
              buffering = false;
              return;
            }
            slot.ignoreUntilFullReplay = false;
          } else {
            return;
          }
        }
        if (buffering) {
          buffer.push(event);
          return;
        }
        // 下一个回合的重放段起头 → 收拢缓冲，按段折。
        if (event.type === "message_start" && opensFullReplaySegment(event)) {
          buffering = true;
          buffer.push(event);
          openGate();
          return;
        }
        dispatchFoldedSseEvent(event, { conversationId, source: "server" });
      },
      (comment) => {
        if (slot.stopped) return;
        if (comment === ATTACH_CAUGHT_UP_COMMENT) {
          if (slot.drainingSelfReplay) {
            slot.drainingSelfReplay = false;
            slot.ignoreUntilFullReplay = false;
            buffer.length = 0;
            buffering = false;
            return;
          }
          if (slot.suspended) return;
          openGate();
        }
      },
    );
    // 边界注释前断流：丢未折缓冲（游标未推进），外层重连拿完整重放。
    // 不得把半段当完成折——那会推游标，下一趟变成残缺增量。
  } finally {
    // 折是异步的（要先拉消息窗）。不等它落地就回到外层重连，两条连接会交叉折同一回合。
    await releasePending;
  }
}

async function runFollowConnection(
  slot: FollowSlot,
): Promise<ConnectionOutcome> {
  const conversationId = slot.conversationId;
  const ac = new AbortController();
  slot.ac = ac;
  try {
    let response = await followFetch(conversationId, ac.signal);
    if (response.status === 401) {
      const refreshed = await tryRefresh();
      if (refreshed === "auth_dead") return "stop";
      if (refreshed !== "renewed") return "retry";
      response = await followFetch(conversationId, ac.signal);
      if (response.status === 401) return "stop";
    }
    if (response.status === 204) {
      // follow 契约是 200 + SSE（空闲心跳）。204 是回合级空闲，不是跟播；
      // 当 ok 空 body 会退避空转打 204。
      logEvent("warn", "conversation.follow_unsupported", {
        conversation_id: conversationId,
      });
      return "stop";
    }
    if (response.status === 403 || response.status === 404) {
      return "stop"; // 会话不存在 / 非本人——重试没有意义
    }
    if (!response.ok || !response.body) return "retry";

    slot.attempts = 0;
    logEvent("info", "conversation.follow_open", {
      conversation_id: conversationId,
    });
    slot.opened = true;
    await pumpFollowBody(slot, response);
    return "ok";
  } catch {
    return "retry"; // 传输失败；让位静音不断这条连接，abort 只来自拆 slot
  } finally {
    if (slot.ac === ac) slot.ac = null;
    slot.opened = false;
  }
}

async function runFollowLoop(slot: FollowSlot): Promise<void> {
  while (!slot.stopped) {
    if (hasLocalConversationStream(slot.conversationId)) {
      slot.suspended = true; // 让位：本端自有连接在折这个会话
      await waitUntilResumable(slot);
      continue;
    }
    slot.suspended = false;
    const outcome = await runFollowConnection(slot);
    if (slot.stopped) return;
    if (outcome === "stop") {
      stopSlot(slot, "server_refused");
      return;
    }
    if (slot.suspended) continue; // 让位导致的断流：立刻回到等待，不退避
    const delay = reconnectBackoffMs(slot.attempts);
    slot.attempts += 1;
    await sleep(slot, delay);
  }
}

function startSlot(conversationId: string): void {
  const slot: FollowSlot = {
    conversationId,
    stopped: false,
    suspended: hasLocalConversationStream(conversationId),
    attempts: 0,
    ac: null,
    opened: false,
    ignoreUntilFullReplay: false,
    drainingSelfReplay: false,
    unsubBusy: () => {},
    wake: null,
  };
  slot.unsubBusy = subscribeLocalConversationStream(conversationId, (busy) =>
    onLocalStreamBusy(slot, busy),
  );
  slots.set(conversationId, slot);
  void runFollowLoop(slot);
}

/**
 * 把对话级订阅移到 ``conversationId``（``null`` = 全关）。幂等：同一会话重复调用不重开流。
 *
 * 同时只留一条订阅——每访问一个会话就多挂一条空闲 SSE 会吃光连接池。切走立刻停。
 *
 * ``closeReason`` 写入 ``follow_closed.reason``。默认 ``switched_away`` = 订阅跟到
 * 另一个会话（含切草稿）。调用方卸订须显式传入 ``local_sidecar`` / ``unsynced``，
 * 不得冒充用户切走。本机 sidecar 活着由 ``beginLocalConversationStream`` 静音，
 * hydrate 不再为此拆 slot。
 *
 * 只管当前会话的回合跟播。跨会话的账号态（队列、被别处结掉的挂起卡）走设备长连接
 * （``accountStateIngress``），不由这条订阅的开合去猜。
 */
export function syncConversationFollow(
  conversationId: string | null,
  closeReason = "switched_away",
): void {
  if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
  for (const slot of [...slots.values()]) {
    if (slot.conversationId !== conversationId) {
      stopSlot(slot, closeReason);
    }
  }
  if (!conversationId) return;
  if (slots.get(conversationId)) return;
  startSlot(conversationId);
}

/** 硬关全部订阅（登出 / 测试隔离）。 */
export function stopAllConversationFollows(): void {
  for (const slot of [...slots.values()]) stopSlot(slot, "stop_all");
  slots.clear();
}

/** 诊断 / 测试：当前挂着订阅的会话。 */
export function followedConversationIds(): string[] {
  return [...slots.keys()];
}
