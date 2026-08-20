import { hasActiveRunningWorkers } from "@/components/graph/helpers";
import { discardAllPendingChunks } from "@/services/sse/contentBuffer";
import { flushPendingFrames } from "@/services/sse/execFrameBuffer";
import { stopConversation } from "@/services/stopTurn";
import {
  execRuntime,
  projectExecution,
  useExecutionStore,
} from "@/stores/execution";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import { createPatchConversation } from "./patchConversation";
import {
  DRAFT_KEY,
  EMPTY_RUNTIME,
  activeRuntime,
  lastAssistantProjectionId,
} from "./runtime";
import type {
  ConversationGet,
  ConversationSet,
  ConversationState,
} from "./state";
import { isTerminalPhase } from "./turnPhase";
import type { Message } from "./types";

type TurnLifecycleActions = Pick<
  ConversationState,
  | "setGenerating"
  | "setAbort"
  | "setTurnPhase"
  | "setExecutionVia"
  | "setWaitingForWorkspaceLock"
  | "stopGeneration"
  | "setError"
  | "clearError"
>;

/** Align with StatusStrip: running, or paused with an in-flight worker (not captain). */
function canStopCurrentExecution(messages: Message[]): boolean {
  const mid = lastAssistantProjectionId(messages);
  if (!mid) return false;
  const ert = execRuntime(useExecutionStore.getState(), mid);
  if (!ert.plan) return false;
  if (ert.status === "running") return true;
  if (ert.status !== "paused") return false;
  const exec = projectExecution(
    ert.plan,
    ert.frames,
    ert.status,
    ert.debate,
    ert.debateRounds,
    ert.crossExamEnabled,
    ert.debateOpening,
  );
  return hasActiveRunningWorkers(exec.runs);
}

/** Turn lifecycle / phase / error / stopGeneration. */
export function createTurnLifecycleActions(
  set: ConversationSet,
  get: ConversationGet,
): TurnLifecycleActions {
  const patchConversation = createPatchConversation(set);

  return {
    setGenerating: (v, conversationId) =>
      patchConversation(conversationId, () => ({ isGenerating: v })),

    setAbort: (a, conversationId) =>
      patchConversation(conversationId, () => ({ abort: a })),

    setTurnPhase: (phase, conversationId) =>
      patchConversation(conversationId, () => ({ turnPhase: phase })),

    setExecutionVia: (via, conversationId) =>
      patchConversation(conversationId, () => ({ executionVia: via })),

    setWaitingForWorkspaceLock: (waiting, conversationId) =>
      patchConversation(conversationId, () => ({
        waitingForWorkspaceLock: waiting,
      })),

    stopGeneration: () => {
      const conversationId = get().currentConversationId;
      const key = conversationId ?? DRAFT_KEY;
      const phase = activeRuntime(get()).turnPhase;

      // GAP A：CEO 已收口（terminal）但 execution 仍可停（含 detached）→ 仅硬取消，
      // 不进入 stopping 等第二次 message_end（会死等）。UI 靠后续 run/execution 帧收口。
      if (isTerminalPhase(phase)) {
        if (
          !conversationId ||
          !canStopCurrentExecution(activeRuntime(get()).messages)
        ) {
          return;
        }
        void stopConversation(conversationId).catch(() => {
          get().setError(
            "停止请求失败，引擎可能仍在运行",
            () => get().stopGeneration(),
            conversationId,
          );
        });
        return;
      }

      if (phase !== "stopping") {
        get().setTurnPhase("stopping", conversationId);
      }

      // stopping 不再适用 wait 心跳；清掉 stamp，避免条上冻着 n/m 假「进行中」。
      const waitMid = lastAssistantProjectionId(activeRuntime(get()).messages);
      if (waitMid) {
        useExecutionStore.getState().setCoordinationWait(null, waitMid);
      }

      // 诚实过渡：落盘已缓冲的 run_*，丢弃正文缓冲；保持 SSE 不断（不 abort），
      // 也不本地伪造 cancelled / finalize——等后端 message_end 定格。
      discardAllPendingChunks(key);
      flushPendingFrames(key);
      clearInteractionPrompts(key);

      if (!conversationId) return;
      void stopConversation(conversationId)
        .then(() => {
          if (get().byId[key]?.turnPhase === "stopping") {
            get().clearError(conversationId);
          }
        })
        .catch(() => {
          const rt = get().byId[key] ?? EMPTY_RUNTIME;
          if (rt.turnPhase !== "stopping") return;
          // 回滚过渡态，允许用户继续看流并再点停止。
          get().setTurnPhase("streaming", conversationId);
          get().setError(
            "停止请求失败，引擎可能仍在运行",
            () => get().stopGeneration(),
            conversationId,
          );
        });
    },

    setError: (message, retry, conversationId, action) =>
      patchConversation(conversationId, () => ({
        error: message,
        retry,
        errorAction: action ?? null,
      })),

    clearError: (conversationId) =>
      patchConversation(conversationId, () => ({
        error: null,
        retry: null,
        errorAction: null,
      })),
  };
}
