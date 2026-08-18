import { syncConversationListPreview } from "@/hooks/useConversations";
import {
  foldAskMarker,
  foldCheckpointMarker,
  foldCitations,
  foldContentDelta,
  foldContentReset,
  foldGraphAppendMarker,
  foldInteractionTimelineMarker,
  foldPlanReviewMarker,
  foldReasoningDelta,
  foldTeamMarker,
  foldTeamPreviewMarker,
  foldToolUseEnd,
  foldToolUsePhase,
  foldToolUseStart,
  foldUserInterjectionMarker,
  messageLaneFromMessage,
} from "@/lib/foldMessageLane";
import { useExecutionStore } from "@/stores/execution";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { createPatchConversation } from "./patchConversation";
import type {
  ConversationGet,
  ConversationSet,
  ConversationState,
} from "./state";
import type { ConversationRuntime, Message } from "./types";

type StreamProjectionActions = Pick<
  ConversationState,
  | "appendToLastMessage"
  | "resetStreamingContent"
  | "appendReasoningToLastMessage"
  | "setComposingTool"
  | "setTraceIdOnLastMessage"
  | "setServerMessageIdOnLastMessage"
  | "resumePausedAssistant"
  | "resetAssistantForNewTurn"
  | "addProcessTool"
  | "endProcessTool"
  | "setProcessToolPhase"
  | "attachCitationsToLastMessage"
  | "attachEvidenceLedgerToLastMessage"
  | "recordTurnWarning"
  | "stampPendingTurnWarning"
  | "recordAutoFolder"
  | "attachCostToLastMessage"
  | "attachTurnMetaToLastMessage"
  | "attachErrorToLastMessage"
  | "stampCheckpointMarker"
  | "stampAskMarker"
  | "stampUserInterjectionMarker"
  | "stampPlanReviewMarker"
  | "stampTeamPreviewMarker"
  | "stampTimelineMarker"
  | "createAssistantMessage"
  | "finalizeLastMessage"
  | "setLastAssistantExecutionId"
  | "stampGraphAppend"
  | "setCaptainContext"
>;

function lastAssistantIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return i;
  }
  return -1;
}

/** Streaming / projection mutations — fold entry points onto the live assistant lane. */
export function createStreamProjectionActions(
  set: ConversationSet,
  get: ConversationGet,
): StreamProjectionActions {
  const patchConversation = createPatchConversation(set);

  return {
    appendToLastMessage: (chunk, conversationId, opts) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last) return null;
        const lane = foldContentDelta(
          messageLaneFromMessage(last),
          chunk,
          opts?.replace,
        );
        messages[messages.length - 1] = {
          ...last,
          content: lane.content,
          process: lane.process,
          composingTool: null,
        };
        return { messages };
      }),

    resetStreamingContent: (reason, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        const lane = foldContentReset(messageLaneFromMessage(last), reason);
        messages[messages.length - 1] = {
          ...last,
          content: lane.content,
          process: lane.process,
        };
        return { messages };
      }),

    appendReasoningToLastMessage: (chunk, conversationId, opts) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last) return null;
        const lane = foldReasoningDelta(
          messageLaneFromMessage(last),
          chunk,
          opts?.replace,
        );
        messages[messages.length - 1] = {
          ...last,
          reasoning: lane.reasoning,
          process: lane.process,
        };
        return { messages };
      }),

    setComposingTool: (tool, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        messages[messages.length - 1] = { ...last, composingTool: tool };
        return { messages };
      }),

    setTraceIdOnLastMessage: (traceId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        if (!traceId) return null;
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        messages[messages.length - 1] = { ...last, traceId };
        return { messages };
      }),

    setServerMessageIdOnLastMessage: (messageId, conversationId) => {
      let clientId: string | null = null;
      patchConversation(conversationId, (rt) => {
        if (!messageId) return null;
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        clientId = last.id;
        messages[messages.length - 1] = { ...last, serverMessageId: messageId };
        return { messages };
      });
      // First stamp: align execution.byId client → server so pause/resume share one key.
      // Also re-key any resume card that surfaced before message_start (client id fallback).
      if (clientId && clientId !== messageId) {
        useExecutionStore.getState().alignTurnKey(clientId, messageId);
        usePausedTurnStore.getState().rekeyMessageId(clientId, messageId);
        useInteractionStore.getState().rekeyMessageId(clientId, messageId);
      }
      // Cold *_required may arrive before message_start with an empty host key
      // (no assistant projection yet). Bind those so selectVisibleColdResumes
      // paints immediately on stamp — no hard-refresh hydrate required.
      const cid = conversationId ?? get().currentConversationId;
      if (cid && messageId) {
        useInteractionStore.getState().bindEmptyMessageId(cid, messageId);
      }
    },

    resetAssistantForNewTurn: (serverMessageId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant" || !last.isStreaming) {
          return null;
        }
        // 同 id 是 pause→resume（调用方已按 existing 分流，这里再守一道）。
        if (last.serverMessageId && last.serverMessageId === serverMessageId) {
          return null;
        }
        const stale =
          last.content !== "" ||
          (last.reasoning ?? "") !== "" ||
          (last.process?.length ?? 0) > 0 ||
          last.composingTool != null;
        if (!stale) return null;
        messages[messages.length - 1] = {
          ...last,
          content: "",
          reasoning: "",
          process: [],
          composingTool: null,
        };
        return { messages };
      }),

    resumePausedAssistant: (serverMessageId, conversationId) => {
      if (!serverMessageId) return null;
      let foundId: string | null = null;
      patchConversation(conversationId, (rt) => {
        const idx = rt.messages.findIndex(
          (m) =>
            m.role === "assistant" &&
            (m.serverMessageId === serverMessageId || m.id === serverMessageId),
        );
        if (idx < 0) return null;
        const messages = [...rt.messages];
        const prev = messages[idx];
        foundId = prev.id;
        messages[idx] = {
          ...prev,
          isStreaming: true,
          serverMessageId: prev.serverMessageId ?? serverMessageId,
          finishReason: undefined,
          outcome: undefined,
          composingTool: null,
        };
        return { messages, isGenerating: true };
      });
      return foundId;
    },

    addProcessTool: (payload, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        const lane = foldToolUseStart(messageLaneFromMessage(last), payload);
        if (lane.process === last.process) return null;
        messages[messages.length - 1] = {
          ...last,
          process: lane.process,
          composingTool: null,
        };
        // 盖章该工具真实开始时刻（桌面本地 · live-only）：ToolLine 据此计「运行 · Ns」，锚定
        // 真实开始而非组件挂载，故行重挂（过程折叠展开 / 聊天列表虚拟化）后仍准。幂等——已存在
        // 不覆盖，重复 tool_use_start 不重置。仅到此处（process 已变=CEO 自身工具新入行）才盖。
        const toolStartedMs =
          rt.toolStartedMs[payload.tool_call_id] !== undefined
            ? rt.toolStartedMs
            : { ...rt.toolStartedMs, [payload.tool_call_id]: Date.now() };
        return { messages, toolStartedMs };
      }),

    endProcessTool: (payload, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        const lane = foldToolUseEnd(messageLaneFromMessage(last), payload);
        if (lane.process === last.process) return null;
        messages[messages.length - 1] = { ...last, process: lane.process };
        // 工具收尾：丢弃其开始时刻（计时已停），避免长会话里 map 累积。
        const patch: Partial<ConversationRuntime> = { messages };
        if (rt.toolStartedMs[payload.tool_call_id] !== undefined) {
          const { [payload.tool_call_id]: _drop, ...rest } = rt.toolStartedMs;
          patch.toolStartedMs = rest;
        }
        return patch;
      }),

    // 工具执行阶段进度 (联网搜索前端展示优化): stamp the running tool step's coarse phase from a
    // (transport-only, live-stream) tool_use_progress event so the waiting UI is honest.
    setProcessToolPhase: (payload, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        const lane = foldToolUsePhase(messageLaneFromMessage(last), payload);
        if (lane.process === last.process) return null;
        messages[messages.length - 1] = { ...last, process: lane.process };
        return { messages };
      }),

    attachCitationsToLastMessage: (citations, conversationId) =>
      patchConversation(conversationId, (rt) => {
        if (citations.length === 0) return null;
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        const lane = foldCitations(messageLaneFromMessage(last), citations);
        messages[messages.length - 1] = { ...last, citations: lane.citations };
        return { messages };
      }),

    attachEvidenceLedgerToLastMessage: (payload, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") return null;
        let next = last.evidenceLedger ?? [];
        if (Array.isArray(payload.entries)) {
          next = payload.entries;
        } else if (payload.delta?.length) {
          const order: string[] = [];
          const byId = new Map<string, (typeof next)[number]>();
          for (const e of next) {
            if (!byId.has(e.id)) order.push(e.id);
            byId.set(e.id, e);
          }
          for (const e of payload.delta) {
            if (!byId.has(e.id)) order.push(e.id);
            byId.set(e.id, e);
          }
          next = order
            .map((id) => byId.get(id))
            .filter((e): e is (typeof next)[number] => e !== undefined);
        } else {
          return null;
        }
        messages[messages.length - 1] = { ...last, evidenceLedger: next };
        return { messages };
      }),

    recordTurnWarning: (warning, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (last?.role === "assistant" && last.isStreaming) {
          messages[messages.length - 1] = { ...last, turnWarning: warning };
          return { messages, pendingTurnWarning: null };
        }
        return { pendingTurnWarning: warning };
      }),

    stampPendingTurnWarning: (conversationId) =>
      patchConversation(conversationId, (rt) => {
        const warning = rt.pendingTurnWarning;
        if (!warning) return null;
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (!last || last.role !== "assistant") {
          return { pendingTurnWarning: warning };
        }
        messages[messages.length - 1] = { ...last, turnWarning: warning };
        return { messages, pendingTurnWarning: null };
      }),

    // 裸聊自动建文件夹告知：事件在 delegate 期间到达，此时助手气泡必然已开，所以不需要
    // turn_warning 那套 pending 缓冲。
    recordAutoFolder: (autoFolder, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx < 0) return null;
        messages[idx] = { ...messages[idx], autoFolder };
        return { messages };
      }),

    attachErrorToLastMessage: (error, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (last && last.role === "assistant") {
          messages[messages.length - 1] = { ...last, error };
        }
        return { messages };
      }),

    attachCostToLastMessage: (cost, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (last && last.role === "assistant") {
          messages[messages.length - 1] = { ...last, cost };
        }
        return { messages };
      }),

    attachTurnMetaToLastMessage: (meta, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (last && last.role === "assistant") {
          messages[messages.length - 1] = {
            ...last,
            ...(meta.usage !== undefined ? { usage: meta.usage } : {}),
            ...(meta.rounds !== undefined ? { rounds: meta.rounds } : {}),
            ...(meta.durationMs !== undefined
              ? { durationMs: meta.durationMs }
              : {}),
            ...(meta.finishReason !== undefined
              ? { finishReason: meta.finishReason }
              : {}),
            ...(meta.collab !== undefined ? { collab: meta.collab } : {}),
            ...(meta.outcome !== undefined ? { outcome: meta.outcome } : {}),
            ...(meta.teamBatch !== undefined
              ? { teamBatch: meta.teamBatch }
              : {}),
          };
        }
        return { messages };
      }),

    stampCheckpointMarker: (checkpointId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        const lane = foldCheckpointMarker(
          messageLaneFromMessage(msg),
          checkpointId,
        );
        messages[idx] = {
          ...msg,
          content: lane.content,
          process: lane.process,
        };
        return { messages };
      }),

    stampAskMarker: (askId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        const lane = foldAskMarker(messageLaneFromMessage(msg), askId);
        messages[idx] = { ...msg, process: lane.process };
        return { messages };
      }),

    stampUserInterjectionMarker: (interjectionId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        const lane = foldUserInterjectionMarker(
          messageLaneFromMessage(msg),
          interjectionId,
        );
        messages[idx] = { ...msg, process: lane.process };
        return { messages };
      }),

    stampPlanReviewMarker: (checkpointId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        const lane = foldPlanReviewMarker(
          messageLaneFromMessage(msg),
          checkpointId,
        );
        messages[idx] = { ...msg, process: lane.process };
        return { messages };
      }),

    stampTeamPreviewMarker: (checkpointId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        const lane = foldTeamPreviewMarker(
          messageLaneFromMessage(msg),
          checkpointId,
        );
        messages[idx] = { ...msg, process: lane.process };
        return { messages };
      }),

    stampTimelineMarker: (marker, id, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        const lane = foldInteractionTimelineMarker(
          messageLaneFromMessage(msg),
          marker,
          id,
        );
        messages[idx] = {
          ...msg,
          content: lane.content,
          process: lane.process,
        };
        return { messages };
      }),

    createAssistantMessage: (conversationId) => {
      const id = crypto.randomUUID();
      patchConversation(conversationId, (rt) => ({
        messages: [
          ...rt.messages,
          {
            id,
            role: "assistant",
            content: "",
            createdAt: new Date().toISOString(),
            executionId: null,
            isStreaming: true,
          },
        ],
        isGenerating: true,
        // Fresh bubble: clear any prior lock-wait chrome（不得静默等锁）.
        waitingForWorkspaceLock: false,
      }));
      return id;
    },

    finalizeLastMessage: (conversationId) => {
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const last = messages[messages.length - 1];
        if (last) {
          messages[messages.length - 1] = {
            ...last,
            isStreaming: false,
            composingTool: null,
          };
        }
        return {
          messages,
          isGenerating: false,
          waitingForWorkspaceLock: false,
        };
      });
      // List cache is hydrate-once (`staleTime: ∞`); bump only moves/updatedAt.
      // Stamp preview here so the sidebar reflects the closed reply.
      const id = conversationId ?? get().currentConversationId;
      if (id) syncConversationListPreview(id);
    },

    setLastAssistantExecutionId: (executionId, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        // 协作图时间线落点: stamp the executionId AND drop a `team` marker fixing the
        // collaboration graph's slot in the CEO timeline (dedup by execution_id, so a
        // debate's two run_plans / a repeat batch only anchor once).
        const lane = foldTeamMarker(messageLaneFromMessage(msg), executionId);
        const idChanged = msg.executionId !== executionId;
        const procChanged = lane.process !== msg.process;
        if (!idChanged && !procChanged) return null;
        messages[idx] = {
          ...msg,
          ...(idChanged ? { executionId } : {}),
          ...(procChanged ? { process: lane.process } : {}),
        };
        return { messages };
      }),

    stampGraphAppend: (payload, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        const msg = messages[idx];
        const lane = foldGraphAppendMarker(
          messageLaneFromMessage(msg),
          payload.execution_id,
          payload.host_message_id,
          payload.added_count,
          payload.act_id,
          payload.act_kind,
          payload.authorized_by,
        );
        if (lane.process === msg.process) return null;
        messages[idx] = { ...msg, process: lane.process };
        return { messages };
      }),

    setCaptainContext: (blocks, conversationId) =>
      patchConversation(conversationId, (rt) => {
        const messages = [...rt.messages];
        const idx = lastAssistantIndex(messages);
        if (idx === -1) return null;
        messages[idx] = { ...messages[idx], captainContext: blocks };
        return { messages };
      }),
  };
}
