import { logEvent } from "@/lib/log";
import { isClientToolRequiredType } from "@/services/clientToolFrames";
import { traceSSEEvent } from "@/services/sseTrace";
import { traceTurnFirstSSE } from "@/services/turnTrace";
import { allowsSseEvent } from "@/stores/conversation/turnPhase";
import { getTurnPhase } from "@/stores/conversation/turnPhaseActions";
import type { SSEEvent } from "@/types/events";
import { handleBoardEvent } from "./handlers/board";
import { handleDesktopEvent } from "./handlers/desktop";
import { handleExecutionEvent } from "./handlers/execution";
import { handleInteractionEvent } from "./handlers/interaction";
import { handleMessageStreamEvent } from "./handlers/messageStream";
import { handleMetaEvent } from "./handlers/meta";
import { handleWorkspaceEvent } from "./handlers/workspace";
import type { DispatchContext } from "./types";

const HANDLERS = [
  handleMessageStreamEvent,
  handleInteractionEvent,
  handleMetaEvent,
  handleWorkspaceEvent,
  handleBoardEvent,
  handleDesktopEvent,
  handleExecutionEvent,
] as const;

/**
 * Single source of truth for SSE event handling.
 *
 * Conversation-level events feed the chat store (single-agent path).
 * `run_*` and tool events feed the execution store — they no-op while no
 * execution exists, so the multi-agent UI lights up automatically once the
 * backend starts emitting them, with zero further frontend wiring.
 *
 * CLIENT_TOOL `*_required` frames never ride this bus in either mode: cloud
 * delivers them on the device fulfill stream, and a sidecar turn delivers them
 * from its own in-process fulfill hub over the `sidecar:fulfill` push. Both land
 * in `services/clientToolIngress`, which owns perform + settle origin.
 */
export function dispatchSSEEvent(event: SSEEvent, ctx: DispatchContext): void {
  // A CLIENT_TOOL frame on the conversation bus is stale (pre-fulfill-hub
  // server): ignore it rather than guess a settle origin — the turnPhase
  // fail-settle below was a conversation-SSE assumption that no longer holds.
  if (isClientToolRequiredType(event.type)) {
    logEvent("warn", "client_tool.ignored_on_conversation_sse", {
      conversation_id: ctx.conversationId,
      event_type: event.type,
      source: ctx.source,
      reason: "fulfill_channel_owns_client_tool",
    });
    return;
  }

  // 停止生命周期事件门：stopping 仍消费 run_* / 队员 tool_use_*，挡正文突变；
  // terminal 只放行终态/meta + 后台 drive 帧。
  const turnPhase = getTurnPhase(ctx.conversationId);
  if (!allowsSseEvent(turnPhase, event.type, event.payload)) {
    // 可观测丢点：勿扫用户长文；只记 event_type / phase（钉门闩 vs 传输丢包）。
    logEvent("warn", "sse.event_dropped", {
      conversation_id: ctx.conversationId,
      event_type: event.type,
      turn_phase: turnPhase,
      reason: "turn_phase_gate",
    });
    return;
  }

  // Dev-only 时序探针（默认关；DevTools 执行 __sseTrace() 开）：记每个事件的到达顺序，
  // 回合末把到达序与气泡 process[] 并排对账。no-op when disabled / in prod.
  traceTurnFirstSSE(ctx.conversationId, event.type);
  traceSSEEvent(event, ctx.conversationId);

  for (const handler of HANDLERS) {
    if (handler(event, ctx)) return;
  }
  // 未知事件类型 = 后端比本客户端新，这是升级期的常态而非异常。编译期穷尽由
  // conformanceFold 的判别联合 switch 负责（那里才有类型收窄）；在这里抛只会让
  // 旧客户端的 catch-up 重放整段中断、连不上，所以忽略并记一条丢点。
  logEvent("warn", "sse.event_dropped", {
    conversation_id: ctx.conversationId,
    event_type: event.type,
    turn_phase: turnPhase,
    reason: "unhandled_event_type",
  });
}

export type { DispatchContext } from "./types";
export {
  discardPendingContent,
  flushPendingContent,
} from "./contentBuffer";
export { flushPendingFrames } from "./execFrameBuffer";
