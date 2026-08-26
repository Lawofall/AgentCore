import { getRuntime, useConversationStore } from "@/stores/conversation";
import {
  allowsStreamingMutations,
  blocksStreamOpen,
} from "@/stores/conversation/turnPhase";
import { getTurnPhase } from "@/stores/conversation/turnPhaseActions";
import { sameTurnStampedServerId } from "./helpers";

/**
 * Ensure the streamed conversation's last message is a streaming assistant
 * message.
 *
 * Backend always emits `message_start` before content, but this stays
 * defensive so a stray `content_delta` never lands on the user bubble. Targets
 * the turn's conversation by id so a background turn opens its bubble on its own
 * slice, not whatever conversation is on screen.
 * Only allowed while turnPhase === streaming (停止后迟到事件不得重建气泡).
 *
 * Same-turn resume continuity: prefer flipping a paused stamped assistant over
 * minting a client-only bubble (otherwise cold `*_required` would bind a key
 * ResumePrompt refuses to paint).
 */
export function ensureStreamingAssistant(conversationId: string): void {
  if (!allowsStreamingMutations(getTurnPhase(conversationId))) return;
  const store = useConversationStore.getState();
  const messages = getRuntime(conversationId).messages;
  const last = messages[messages.length - 1];
  if (last?.role === "assistant" && last.isStreaming) {
    store.stampPendingTraceId(conversationId);
    return;
  }

  if (last?.role === "assistant" && last.serverMessageId) {
    if (store.resumePausedAssistant(last.serverMessageId, conversationId)) {
      return;
    }
  }

  // Orphan unstamped tail after a same-turn stamped host: resume the host and
  // inherit its stamp onto the streaming slot rather than minting another UUID.
  const stamp = sameTurnStampedServerId(conversationId);
  if (stamp) {
    if (store.resumePausedAssistant(stamp, conversationId)) {
      return;
    }
    store.createAssistantMessage(conversationId);
    store.setServerMessageIdOnLastMessage(stamp, conversationId);
    return;
  }

  store.createAssistantMessage(conversationId);
}

type PendingChunk = {
  kind: "content" | "reasoning";
  text: string;
  /** attach 增量重放的替换帧：`text` 是末尾未闭合块的全文，写出时换块而非追加。 */
  replace?: boolean;
};

/**
 * rAF 合批 CEO 气泡的流式正文 + 思考（content_delta / reasoning_delta，流式渲染性能）。
 *
 * 后端逐 token 推 content_delta / reasoning_delta，每个都直接写 store 会让 Markdown / 思考区
 * 每 token 全量重渲染（叠加块级记忆化前尤甚），逐 token 叠加即整条流 O(n²)——「长输出白屏
 * 卡死」的 CEO 气泡侧根因。这里把同一会话「一帧内」到达的 delta 攒成一批，在下一次 animation
 * frame 一次性 append——把每秒上百次 store 写入降到 ≤60 次。按 conversationId 分桶，故多个
 * 后台会话各自合批、互不串台。
 *
 * 正文与思考是同一气泡上的两个独立字段（互不拼接），共享同一条回合生命周期，故共用一个 rAF。
 * Chunks 按**到达顺序**入队（相邻同类型合并），flush 时 FIFO 写出——保证 `process[]` fold
 * 顺序与 SSE 到达顺序一致。旧实现用两个桶且 flush 时固定先 content 后 reasoning，同帧交错
 * 会把「先思考后正文」折成「正文→思考」并在 process 时间线里拆出多个思考块。
 *
 * 必须在回合收尾前 flush：`appendToLastMessage` / `appendReasoningToLastMessage` 都不校验
 * `isStreaming`，缓冲若漏到收尾之后，rAF 回调会把尾 token 追加到已结束（极端情况下是下一条）
 * 的消息上。故 `message_end` / `error` 分支会先 flush，传输层 finally 再兜底 flush。
 */
const pendingChunks = new Map<string, PendingChunk[]>();
const pendingFrame = new Map<string, number>();

function cancelFrame(conversationId: string): void {
  const frame = pendingFrame.get(conversationId);
  if (frame !== undefined) {
    cancelAnimationFrame(frame);
    pendingFrame.delete(conversationId);
  }
}

function enqueueChunk(
  conversationId: string,
  kind: PendingChunk["kind"],
  delta: string,
  replace = false,
): void {
  if (!delta) return;
  let q = pendingChunks.get(conversationId);
  if (!q) {
    q = [];
    pendingChunks.set(conversationId, q);
  }
  const last = q[q.length - 1];
  // 替换帧自己起一块——并进前一块会把「换掉末尾未闭合块」变成「接在它后面」。反向合并
  // 是安全的：先换成 A 再追加 B，等价于换成 A+B，所以替换块后面的追加照常并入。
  if (last?.kind === kind && !replace) last.text += delta;
  else q.push({ kind, text: delta, replace });
  scheduleFlush(conversationId);
}

/** 立即写出某会话已缓冲的正文+思考（按到达顺序），并取消其挂起的 frame。无缓冲时为 no-op。
 * stopping/terminal 态丢弃缓冲（不 append），避免停止后迟到 rAF 把 UI 拉回生成态。 */
export function flushPendingContent(conversationId: string): void {
  cancelFrame(conversationId);
  const q = pendingChunks.get(conversationId);
  if (!q?.length) return;
  pendingChunks.delete(conversationId);
  if (blocksStreamOpen(getTurnPhase(conversationId))) return;
  const store = useConversationStore.getState();
  for (const chunk of q) {
    const opts = { replace: chunk.replace };
    if (chunk.kind === "content") {
      store.appendToLastMessage(chunk.text, conversationId, opts);
    } else {
      store.appendReasoningToLastMessage(chunk.text, conversationId, opts);
    }
  }
}

/** 丢弃某会话全部未写出缓冲（正文+思考），取消挂起 frame。停止生成时用。 */
export function discardAllPendingChunks(conversationId: string): void {
  cancelFrame(conversationId);
  pendingChunks.delete(conversationId);
}

/** 丢弃某会话已缓冲但未写出的**正文**（取消挂起 frame，且不 append）。`content_reset` 用：
 * 那批 delta 属于被交付前核验否决的违规正文，无需落到气泡。思考不受影响（其未被否决）；若无
 * 待写 chunk 则一并取消挂起 frame。无缓冲时为 no-op。 */
export function discardPendingContent(conversationId: string): void {
  const q = pendingChunks.get(conversationId);
  if (!q) return;
  const kept = q.filter((c) => c.kind === "reasoning");
  if (kept.length === 0) {
    pendingChunks.delete(conversationId);
    cancelFrame(conversationId);
  } else {
    pendingChunks.set(conversationId, kept);
  }
}

function scheduleFlush(conversationId: string): void {
  if (pendingFrame.has(conversationId)) return;
  const frame = requestAnimationFrame(() => {
    pendingFrame.delete(conversationId);
    flushPendingContent(conversationId);
  });
  pendingFrame.set(conversationId, frame);
}

/** 把一段正文 delta 入队，并确保已排定一次 frame flush。`replace` 见 {@link PendingChunk}。 */
export function queueContentDelta(
  conversationId: string,
  delta: string,
  replace?: boolean,
): void {
  enqueueChunk(conversationId, "content", delta, replace);
}

/** 把一段思考(reasoning) delta 入队，并确保已排定一次 frame flush（与正文共用 rAF）。 */
export function queueReasoningDelta(
  conversationId: string,
  delta: string,
  replace?: boolean,
): void {
  enqueueChunk(conversationId, "reasoning", delta, replace);
}
