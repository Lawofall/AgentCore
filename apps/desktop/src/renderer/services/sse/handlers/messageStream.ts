import { logEvent } from "@/lib/log";
import { queryClient } from "@/lib/queryClient";
import { conversationKeys } from "@/lib/queryKeys";
import { parseResumeDeferredPayload } from "@/lib/resumeDeferred";
import { parseResumeSettledPayload } from "@/lib/resumeSettled";
import { parseTurnOutcomeKind } from "@/lib/turnOutcome";
import { surfaceResumeFromLiveTurn } from "@/services/resume";
import { traceTurnEnd } from "@/services/sseTrace";
import { clearQueuedTurnLocally } from "@/services/turns/cancelQueuedTurn";
import { settleConsumedResume } from "@/services/turns/consumedResume";
import { notifySteerDegradedToQueue } from "@/services/turns/queuedNotify";
import {
  completeTurnPhase,
  getRuntime,
  getTurnPhase,
  isTerminalPhase,
  lastAssistantProjectionId,
  useConversationStore,
} from "@/stores/conversation";
import {
  execRuntime,
  hasUnsettledRuns,
  useExecutionStore,
} from "@/stores/execution";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import type {
  AutoFolderCreatedPayload,
  ContentDeltaPayload,
  ContentResetPayload,
  ErrorPayload,
  MessageEndPayload,
  MessageStartPayload,
  ReasoningDeltaPayload,
  SSEEvent,
  ToolProgressPayload,
  TurnQueueCancelledPayload,
  TurnQueueStartedPayload,
  TurnQueuedPayload,
  TurnWarningPayload,
  WorkspaceLockWaitPayload,
} from "@/types/events";
import { resetCaptainContext } from "../captainContext";
import {
  discardAllPendingChunks,
  discardPendingContent,
  ensureStreamingAssistant,
  flushPendingContent,
  queueContentDelta,
  queueReasoningDelta,
} from "../contentBuffer";
import { flushPendingFrames } from "../execFrameBuffer";
import type { DispatchContext } from "../types";

function finalizeTurnTrace(conversationId: string): void {
  const msgs = getRuntime(conversationId).messages;
  const lastA = [...msgs].reverse().find((m) => m.role === "assistant");
  traceTurnEnd(conversationId, lastA?.process);
}

export function handleMessageStreamEvent(
  event: SSEEvent,
  ctx: DispatchContext,
): boolean {
  const { conversationId } = ctx;

  switch (event.type) {
    case "turn_queued": {
      // EPHEMERAL =「队列变了」信号。内容与排序都由设备通道的整队快照负责
      // （`accountStateIngress`），本帧只管这条流上说得出、快照说不出的那件事：
      // 普通排队条即反馈不 toast，steer 降级必须 toast（光看条看不出降级）。
      const p = event.payload as TurnQueuedPayload;
      if (p.degraded_from === "steer") {
        notifySteerDegradedToQueue();
      }
      return true;
    }
    case "turn_queue_started": {
      // EPHEMERAL：FIFO 出队开跑（新回合 sink 首帧，先于 message_start）。
      // 按 queue_id 清 QueuedTurnsBar；用户泡由 midFlight 在本帧前补插。
      // 剩下几条的序号由随后到达的整队快照重排。
      const p = event.payload as TurnQueueStartedPayload;
      useQueuedTurnsStore.getState().remove(conversationId, p.queue_id);
      return true;
    }
    case "turn_queue_cancelled": {
      // EPHEMERAL：多端同步清排队 UI（本地 cancel 已清则幂等 no-op）。
      const p = event.payload as TurnQueueCancelledPayload;
      clearQueuedTurnLocally(conversationId, p.queue_id);
      return true;
    }
    case "resume_deferred": {
      // EPHEMERAL：settlement 已锁；戳 IX，卡面「已记下」，同连接等待。非错误。
      const p = parseResumeDeferredPayload(event.payload);
      if (p) {
        useInteractionStore.getState().markResumeDeferred({
          conversationId: p.conversation_id || conversationId,
          messageId: p.message_id,
          busyReason: p.busy_reason,
        });
      }
      return true;
    }
    case "resume_settled": {
      // EPHEMERAL：这张卡的帧已被上一次续跑吃掉，服务端回 200 + 事实帧而不是 404。
      // 卡收成结果态（记下决策 / 落定时刻 / 回合状态，但不认领处理方）；壳一并丢掉——
      // 本帧就是「帧不在了」的证据。
      const p = parseResumeSettledPayload(event.payload);
      if (!p) return true;
      const cid = p.conversation_id || conversationId;
      useInteractionStore.getState().markResumeSettled({
        id: p.checkpoint_id,
        kind: p.kind,
        conversationId: cid,
        messageId: p.message_id,
        decision: p.decision,
        decidedAt: p.decided_at,
        turnStatus: p.turn_status,
      });
      usePausedTurnStore.getState().removeByCheckpoint(p.checkpoint_id);
      // running = 同连接紧接着就是那次续跑的实时流：什么都别做，让它照常流下去
      //（用户点了「继续」，AI 正在继续，他就该无缝看着它继续）。
      if (p.turn_status !== "running") {
        settleConsumedResume(cid, p.message_id);
      }
      return true;
    }
    case "turn_warning": {
      const payload = event.payload as TurnWarningPayload;
      useConversationStore
        .getState()
        .recordTurnWarning(payload.message, conversationId);
      return true;
    }
    case "auto_folder_created": {
      // 裸聊写盘自动建了云文件夹：气泡里出轻提示（告知落点，不挡回合）。文件夹列表随即
      // 刷新，提示上的「打开」「改名」才有真东西可指。
      const p = event.payload as AutoFolderCreatedPayload;
      useConversationStore
        .getState()
        .recordAutoFolder(
          { folderId: p.folder_id, name: p.name },
          conversationId,
        );
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.grouped,
      });
      return true;
    }
    case "workspace_lock_wait": {
      // EPHEMERAL：写锁短等 — 空气泡显示「等待工作区…」而非 Thinking…（不得静默等锁）。
      const p = event.payload as WorkspaceLockWaitPayload;
      useConversationStore
        .getState()
        .setWaitingForWorkspaceLock(Boolean(p.waiting), conversationId);
      return true;
    }
    case "message_start": {
      const payload = event.payload as MessageStartPayload;
      // Resume = same-turn continuation: if an assistant already matches the
      // server message_id, reuse it (idempotent). Never delete+create.
      const store = useConversationStore.getState();
      store.setWaitingForWorkspaceLock(false, conversationId);
      // 跨回合回放 / 同连接下一回合：上一回合 message_end 已进 terminal，须先拨回
      // streaming，否则 ensureStreamingAssistant 与后续生长帧会被门禁丢掉。
      if (isTerminalPhase(getTurnPhase(conversationId))) {
        store.setTurnPhase("streaming", conversationId);
      }
      const existing = payload.message_id
        ? getRuntime(conversationId).messages.find(
            (m) =>
              m.role === "assistant" &&
              (m.id === payload.message_id ||
                m.serverMessageId === payload.message_id),
          )
        : undefined;
      if (existing) {
        if (!existing.isStreaming) {
          store.resumePausedAssistant(payload.message_id, conversationId);
        } else {
          store.setGenerating(true, conversationId);
        }
      } else {
        // Cross-turn: last assistant already stamped under a different server id —
        // mint a fresh bubble. ensureStreamingAssistant would resumePausedAssistant
        // (same-turn cold-resume path) and wipe the prior turn's process/team.
        const last = getRuntime(conversationId).messages.at(-1);
        if (
          last?.role === "assistant" &&
          last.serverMessageId &&
          last.serverMessageId !== payload.message_id
        ) {
          if (last.isStreaming) {
            store.finalizeLastMessage(conversationId);
          }
          store.createAssistantMessage(conversationId);
        } else {
          ensureStreamingAssistant(conversationId);
        }
        // 换回合（陌生 message_id）：复用的尾部占位气泡若带着上一段生命的残留
        // 正文/思考/过程（如被上一回合回放污染的乐观占位），先清干净再开流——
        // 对齐 conformanceFold 的 message_start 语义（message_id 变化 ⇒ 空正文），
        // 消除 live/fold 漂移。未写出的 rAF 缓冲同属上一段生命，一并丢弃。
        discardAllPendingChunks(conversationId);
        store.resetAssistantForNewTurn(payload.message_id, conversationId);
        store.setGenerating(true, conversationId);
      }
      // 排队条出队真相源 = turn_queue_started（勿再靠 message_start 猜末条用户泡）。
      store.stampPendingTurnWarning(conversationId);
      if (payload.trace_id)
        store.setTraceIdOnLastMessage(payload.trace_id, conversationId);
      // Stamp server turn id (and one-time align execution client→server).
      store.setServerMessageIdOnLastMessage(payload.message_id, conversationId);
      // Turn (re)start — clear the captain context accumulator so a reconnect replay
      // (which re-sends message_start first) rebuilds it idempotently (上下文传递可视化 通道①+⑤).
      resetCaptainContext(conversationId);
      return true;
    }
    case "content_delta": {
      ensureStreamingAssistant(conversationId);
      // `replace`（attach 增量重放）：这帧带的是末尾未闭合正文块的全文，换块而非追加。
      const p = event.payload as ContentDeltaPayload;
      queueContentDelta(conversationId, p.delta, p.replace);
      return true;
    }
    case "content_reset": {
      discardPendingContent(conversationId);
      useConversationStore
        .getState()
        .resetStreamingContent(
          (event.payload as ContentResetPayload).reason,
          conversationId,
        );
      return true;
    }
    case "reasoning_delta": {
      ensureStreamingAssistant(conversationId);
      // rAF 合批思考流 (流式性能): 与正文共用一条 rAF、同点 flush，避免逐 token 写 store。
      const p = event.payload as ReasoningDeltaPayload;
      queueReasoningDelta(conversationId, p.delta, p.replace);
      return true;
    }
    case "tool_progress": {
      ensureStreamingAssistant(conversationId);
      const p = event.payload as ToolProgressPayload;
      useConversationStore
        .getState()
        .setComposingTool(
          { toolName: p.tool_name, chars: p.chars },
          conversationId,
        );
      return true;
    }
    case "message_end": {
      flushPendingContent(conversationId);
      // Land any rAF-buffered worker frames before the turn finalizes so the graph's
      // last deltas aren't dropped on a fast end (流式性能合批的收尾兜底).
      flushPendingFrames(conversationId);
      const payload = event.payload as MessageEndPayload;
      const conv = useConversationStore.getState();
      if (payload.cost) {
        conv.attachCostToLastMessage(payload.cost, conversationId);
      }
      const usage = payload.usage;
      conv.attachTurnMetaToLastMessage(
        {
          usage: usage
            ? {
                input: usage.input_tokens,
                output: usage.output_tokens,
                reasoning: usage.reasoning_tokens,
                cache_hit: usage.cache_hit_tokens,
                cache_miss: usage.cache_miss_tokens,
              }
            : undefined,
          rounds: payload.rounds,
          durationMs:
            typeof payload.duration_ms === "number"
              ? payload.duration_ms
              : undefined,
          finishReason: payload.finish_reason,
          collab: payload.collab,
          outcome: payload.outcome ?? null,
          teamBatch: payload.team_batch,
        },
        conversationId,
      );
      conv.finalizeLastMessage(conversationId);
      clearInteractionPrompts(conversationId);
      // 挂起即收口 (②): a turn can END at a durable checkpoint — message_end carries
      // finish_reason=paused. The turn is NOT done: its frame was persisted and its
      // in-process resolve Future was never parked, so keep the graph paused (not
      // "completed") and let the now-dormant inline checkpoint card hand off to the
      // (single) durable resume card, surfaced from the *_required payload already on the
      // bubble (no /recovery round-trip → reproduces offline in #/preview).
      const paused = payload.finish_reason === "paused";
      // 只收口【本回合】助手槽；跨回合同图追加时生长帧在宿主卡，不得被追加回合
      // message_end 误标 completed（图完成态由 execution 内 run 终态 reconcile）。
      const mid = lastAssistantProjectionId(
        getRuntime(conversationId).messages,
      );
      if (mid) {
        const attested = parseTurnOutcomeKind(payload.outcome);
        if (attested && useExecutionStore.getState().byId[mid]) {
          useExecutionStore.getState().setAttestedOutcome(attested, mid);
        }
        const rt = execRuntime(useExecutionStore.getState(), mid);
        if (rt.plan) {
          // 后台托管继续跑 (coordination.turn_detached): CEO 回合结束时图内仍有
          // running/pending **worker** —— 不塌成 completed（否则状态条冻在残缺计数、
          // finalizeFold 把未跑节点标「未执行」，而其余节点还显示「执行中」）。保持
          // running，交由 recordFrame(s) 的 run 终态 reconcile 在最后一个托管 worker
          // 终态帧落时收口（经重连回放 / 跨回合追加送达）。paused 收口与「工人已终态」
          // 两条路径不变。Captain 假 pending（pre-plan run_started 被丢）不参与 hold，
          // 否则 end_turn 后会永久钉在「正在生成汇总」。
          // cancelled/interrupted：后端终态权威，立刻定格（finalizeFold 冻残留 running）。
          // attested/finish paused wins over a preceding error event's failed stamp
          // so CEO 汇总 stays pending, not a second red failure.
          const cancelled =
            payload.finish_reason === "cancelled" ||
            payload.finish_reason === "interrupted";
          if (paused || attested === "paused") {
            useExecutionStore.getState().setStatus("paused", mid);
          } else if (rt.status !== "failed") {
            if (cancelled) {
              useExecutionStore.getState().setStatus("cancelled", mid);
            } else if (!hasUnsettledRuns(rt)) {
              useExecutionStore.getState().setStatus("completed", mid);
            }
          }
        }
      }
      // Idle slice eviction is LRU-only on switchConversation — do not drop the
      // complete window here (message-window write contract step 2).
      logEvent("info", "conversation.slice_diag", {
        action: "message_end_slice_kept",
        conversation_id: conversationId,
        active_id: useConversationStore.getState().currentConversationId,
        still_in_memory: Boolean(
          useConversationStore.getState().byId[conversationId],
        ),
        finish_reason: payload.finish_reason ?? null,
      });
      finalizeTurnTrace(conversationId);
      if (paused) surfaceResumeFromLiveTurn(conversationId, ctx.source);
      // 正常完成 / 停止确认：推进生命周期。stopping → stopped；其余 → completed。
      // 超时已进 terminal 则不覆盖（避免 stopped 被迟到 message_end 改成 completed）。
      // attested/finish paused is a settled pause, not a failure — override a
      // preceding error event's failed phase so continue can open a stream.
      const phase = getTurnPhase(conversationId);
      if (phase === "stopping") {
        completeTurnPhase(conversationId, "stopped");
      } else if (paused || parseTurnOutcomeKind(payload.outcome) === "paused") {
        if (phase !== "stopped") {
          completeTurnPhase(conversationId, "completed");
        }
      } else if (!isTerminalPhase(phase)) {
        completeTurnPhase(conversationId, "completed");
      }
      return true;
    }
    case "error": {
      // terminal 后迟到 error：turnPhase 本就因守卫不改；消息/协作图侧效也须
      // no-op，否则会出现「phase=completed 但气泡挂 error、图被打 failed」的自相矛盾。
      // stopping/streaming 仍正常收口。allowsSseEvent 放行的 run_*/execution_* 不经此分支。
      if (isTerminalPhase(getTurnPhase(conversationId))) {
        return true;
      }
      flushPendingContent(conversationId);
      flushPendingFrames(conversationId);
      ensureStreamingAssistant(conversationId);
      const store = useConversationStore.getState();
      const payload = event.payload as ErrorPayload;
      store.attachErrorToLastMessage(
        {
          code: payload.code,
          message: payload.message,
          context: payload.context,
        },
        conversationId,
      );
      store.finalizeLastMessage(conversationId);
      clearInteractionPrompts(conversationId);
      const mid = lastAssistantProjectionId(
        getRuntime(conversationId).messages,
      );
      if (mid && execRuntime(useExecutionStore.getState(), mid).plan) {
        useExecutionStore.getState().setStatus("failed", mid);
      }
      // Same as message_end: keep the complete window; idle prune is LRU-only.
      finalizeTurnTrace(conversationId);
      completeTurnPhase(
        conversationId,
        getTurnPhase(conversationId) === "stopping" ? "stopped" : "failed",
      );
      return true;
    }
    default:
      return false;
  }
}
