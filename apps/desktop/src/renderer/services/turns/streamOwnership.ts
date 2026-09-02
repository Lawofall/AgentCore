/**
 * 会话级 SSE 主路所有权（发送即有流 · midFlight 双连接防交叉）。
 *
 * turn1 的 POST/attach/sidecar 泵持有 primary；midFlight 经典排队在 ``turn_queued``
 * 之后缓冲后续帧，直到 primary 栈清空（turn1 含 turn_saved 等 meta 的整段泵结束）
 * 再放行 ``message_start``——避免 drain 边界处 resetAssistant 与 turn1 收口帧交错
 * 污染末条气泡。
 *
 * 协调插话不经此门（短确认流即时 dispatch）。入队发生在 POST 时刻（D9 FIFO），
 * 本模块只推迟客户端 fold，不推迟服务端排队。
 */

type Slot = {
  /** 嵌套 claim 栈（sendTurn 外包 + runMessageStream 内包）；空 = 主路空闲。 */
  stack: string[];
  waiters: Array<() => void>;
};

const slots = new Map<string, Slot>();

function slotOf(conversationId: string): Slot {
  let s = slots.get(conversationId);
  if (!s) {
    s = { stack: [], waiters: [] };
    slots.set(conversationId, s);
  }
  return s;
}

export function claimPrimaryStream(conversationId: string): string {
  const token = crypto.randomUUID();
  slotOf(conversationId).stack.push(token);
  return token;
}

export function releasePrimaryStream(
  conversationId: string,
  token: string,
): void {
  const s = slots.get(conversationId);
  if (!s) return;
  const idx = s.stack.lastIndexOf(token);
  if (idx < 0) return;
  s.stack.splice(idx, 1);
  if (s.stack.length === 0) {
    const waiters = s.waiters.splice(0);
    for (const w of waiters) w();
    if (s.waiters.length === 0 && s.stack.length === 0) {
      slots.delete(conversationId);
    }
  }
}

export function isPrimaryStreamIdle(conversationId: string): boolean {
  const s = slots.get(conversationId);
  return !s || s.stack.length === 0;
}

/** 主路变为空闲时回调一次（已空闲则仍登记，等下次 release；即时空闲请先查 idle）。 */
export function onPrimaryStreamIdle(
  conversationId: string,
  cb: () => void,
): () => void {
  const s = slotOf(conversationId);
  s.waiters.push(cb);
  return () => {
    const i = s.waiters.indexOf(cb);
    if (i >= 0) s.waiters.splice(i, 1);
  };
}

export function waitForPrimaryStreamIdle(
  conversationId: string,
): Promise<void> {
  if (isPrimaryStreamIdle(conversationId)) return Promise.resolve();
  return new Promise((resolve) => {
    const unsub = onPrimaryStreamIdle(conversationId, () => {
      unsub();
      resolve();
    });
  });
}

/**
 * 本端自有会话连接闸（对话级订阅互斥 · 云对话多端同权 B2 · P0-b）。
 *
 * primary 栈排的是同端两条连接的 fold **次序**；这里排的是「对话级长订阅
 * （``GET …/stream?follow=true``）不得与本端自己开的回合连接同折一个回合」——
 * 那会把同一回合折两次。跟播侧静音不断连，见 ``conversationFollow``。
 *
 * 不能复用 primary 栈：midFlight 的 primary claim 必须等 drain 才拿（提前拿会自锁
 * 自己的 ``waitForPrimaryStreamIdle``），可它的 POST 从发出那一刻起，服务端就已经
 * 排出了新回合——对订阅方而言它从那时起就该静音。
 */
type LocalStreamSlot = {
  /** 当前打开的本端连接数（回合流嵌套 / midFlight 并发）。 */
  count: number;
  /**
   * Bumped by {@link forceReleaseLocalConversationStream} so later `finally`
   * releases from the leftover claim are no-ops (count must not go negative).
   */
  generation: number;
  listeners: Set<(busy: boolean) => void>;
};

const localStreams = new Map<string, LocalStreamSlot>();

function localSlotOf(conversationId: string): LocalStreamSlot {
  let slot = localStreams.get(conversationId);
  if (!slot) {
    slot = { count: 0, generation: 0, listeners: new Set() };
    localStreams.set(conversationId, slot);
  }
  return slot;
}

function dropLocalSlotIfEmpty(conversationId: string): void {
  const slot = localStreams.get(conversationId);
  if (slot && slot.count === 0 && slot.listeners.size === 0) {
    localStreams.delete(conversationId);
  }
}

/**
 * 声明本端为该会话开了自有 SSE（POST 回合流 / 回合级 attach / midFlight 排队连接）。
 *
 * 同步通知订阅者（对话级订阅据此立刻**静音**，不断 SSE），故必须在**发出请求之前**调用；返回的释放
 * 函数幂等，调用方在 ``finally`` 里调一次即可。
 */
export function beginLocalConversationStream(
  conversationId: string,
): () => void {
  const slot = localSlotOf(conversationId);
  const generation = slot.generation;
  slot.count += 1;
  if (slot.count === 1) {
    for (const cb of [...slot.listeners]) cb(true);
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (slot.generation !== generation) return;
    if (slot.count <= 0) return;
    slot.count -= 1;
    if (slot.count > 0) return;
    for (const cb of [...slot.listeners]) cb(false);
    dropLocalSlotIfEmpty(conversationId);
  };
}

export function hasLocalConversationStream(conversationId: string): boolean {
  return (localStreams.get(conversationId)?.count ?? 0) > 0;
}

/**
 * 本机流空闲时回调一次：已空闲则同步触发；否则等下一次 `count → 0`。
 *
 * Harvest 写回 softRefresh 用这条等活用户回合自然释放，禁止
 * {@link forceReleaseLocalConversationStream} 掐活流。
 */
export function whenLocalConversationStreamIdle(
  conversationId: string,
  cb: () => void,
): () => void {
  let settled = false;
  const finish = (): void => {
    if (settled) return;
    settled = true;
    cb();
  };
  if (!hasLocalConversationStream(conversationId)) {
    finish();
    return () => {
      settled = true;
    };
  }
  const unsub = subscribeLocalConversationStream(conversationId, (busy) => {
    if (busy) return;
    unsub();
    finish();
  });
  if (!hasLocalConversationStream(conversationId)) {
    unsub();
    finish();
  }
  return () => {
    settled = true;
    unsub();
  };
}

/**
 * Drop leftover local-stream claims and invalidate in-flight release closures.
 *
 * Harvest write-back must not call this on a live user turn — wait for idle
 * ({@link whenLocalConversationStreamIdle}) instead.
 */
export function forceReleaseLocalConversationStream(
  conversationId: string,
): boolean {
  const slot = localStreams.get(conversationId);
  if (!slot || slot.count === 0) return false;
  slot.generation += 1;
  slot.count = 0;
  for (const cb of [...slot.listeners]) cb(false);
  dropLocalSlotIfEmpty(conversationId);
  return true;
}

/** 订阅本端自有连接的忙/闲翻转（对话级订阅用来让位 / 复位）。 */
export function subscribeLocalConversationStream(
  conversationId: string,
  cb: (busy: boolean) => void,
): () => void {
  const slot = localSlotOf(conversationId);
  slot.listeners.add(cb);
  return () => {
    slot.listeners.delete(cb);
    dropLocalSlotIfEmpty(conversationId);
  };
}

/** 测试隔离：清空所有会话的所有权态。 */
export function resetStreamOwnershipForTests(): void {
  slots.clear();
  localStreams.clear();
}
