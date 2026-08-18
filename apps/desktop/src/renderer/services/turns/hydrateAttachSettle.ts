/**
 * Open-time attach/settle after message-window fetch (P4 unified hydrate).
 *
 * Decoupled from message-window adopt and from hydrate UI ready: ConversationPage
 * reveals after adopt (message window / cache); recovery+attach stay eager in the
 * background via {@link scheduleHydrateAttachSettle} and must not cover already-adopted
 * text. `loadRecovery` never rejects.
 * Warm reopen keeps the in-memory slice (adopt skips overwrite) but still runs
 * recovery-driven attach/settle so a detached live / ghost running assistant is
 * not left stuck in a fake generating state.
 *
 * 观察泵挂在会话切片上：切会话 ≠ 卸观察。本路径不接受页级 AbortSignal；
 * 显式卸观察仅由 `attachSidecarTurn({ signal })` 调用方传入。
 */
import { logEvent } from "@/lib/log";
import {
  type ConversationRecovery,
  shouldHydrateLocalRecovery,
} from "@/services/resume";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { syncConversationFollow } from "./conversationFollow";
import { projectPausedRuns } from "./projectPausedRuns";
import { projectUnsyncedTurns } from "./projectUnsynced";
import {
  attachOnOpen,
  settleCloudRunningAssistant,
  settleOrphanEmptyAssistants,
} from "./recovery";
import { attachSidecarTurn } from "./sidecarAttach";
import {
  beginLocalConversationStream,
  hasLocalConversationStream,
} from "./streamOwnership";

/** Kick attach/settle when recovery lands. Does not delay overlay reveal. */
export function scheduleHydrateAttachSettle(
  conversationId: string,
  recoveryLoaded: Promise<ConversationRecovery>,
): void {
  void recoveryLoaded.then((recovery) => {
    void runHydrateAttachSettle(conversationId, recovery);
  });
}

/**
 * Branch on recovery facts and rejoin / settle / project unsynced.
 *
 * Cloud path reads the **runtime** tail message (not the fetched window): after
 * a successful cold adopt they match; on warm reopen memory may already be newer.
 */
export async function runHydrateAttachSettle(
  conversationId: string,
  recovery: ConversationRecovery,
): Promise<"local" | "cloud"> {
  const useLocal = shouldHydrateLocalRecovery(recovery);
  logEvent("info", "conversation.hydrate", {
    conversation_id: conversationId,
    sidecar_live: recovery.sidecarLive,
    cloud_live: recovery.cloudLive,
    unsynced_count: recovery.unsynced.length,
    paused_count: recovery.pausedCount,
    branch: useLocal ? "local" : "cloud",
  });
  // 对话级订阅由揭窗立刻 sync(id)。unsynced 仍卸订（服务端没有 run）。
  // 本机 sidecar 活着不拆 slot：本端连接闸挂起，回合结束后 follow 自动连回。
  // 迟到的 hydrate 不抢订：已切走则不动全局那一条。
  // 卸订 ≠ 用户切走：follow_closed.reason 必须是让位因由，禁止冒充 switched_away。
  if (
    useConversationStore.getState().currentConversationId === conversationId
  ) {
    if (recovery.unsynced.length > 0) {
      syncConversationFollow(null, "unsynced");
    }
    // 打开对话不再清 `ai_attention`：权威是 fulfill 快照 / 增量。当前页 banner
    // 自己过滤；侧栏灯必须留下，否则帽外 required 一进对话就灭。
  }
  // 本端连接闸已占用 — attach* 不得再开一条。Cold overlay 的 isGenerating 不是所有权。
  if (hasLocalConversationStream(conversationId)) {
    return useLocal ? "local" : "cloud";
  }
  if (useLocal) {
    // 判定与 attachSidecarTurn.beginLocal 之间 follow 仍活着；先占闸，避免
    // 段首 full_replay 被跟播再被 sidecar 快照折一遍。嵌套 claim，回合结束才放。
    const releaseSidecarYield =
      recovery.sidecarLive && recovery.pausedCount === 0
        ? beginLocalConversationStream(conversationId)
        : null;
    try {
      projectUnsyncedTurns(conversationId, recovery.unsynced);
      // Paused local turns skip attach (no live buffer). Cloud pause writeback
      // omits turn_journal, so reinject display runs from the pause frame.
      if (recovery.pausedCount > 0) {
        projectPausedRuns(conversationId, recovery.pausedRuns ?? {});
      }
      // After unsynced project: seal any blank open/ghost assistants as「已中断」.
      settleOrphanEmptyAssistants(conversationId);
      if (recovery.sidecarLive && recovery.pausedCount === 0) {
        // 切会话不卸观察泵 — 无页级 signal。
        await attachSidecarTurn(conversationId);
      }
      return "local";
    } finally {
      releaseSidecarYield?.();
    }
  }
  const last = getRuntime(conversationId).messages.at(-1);
  if (last) {
    const canAttach = recovery.cloudLive && recovery.pausedCount === 0;
    if (last.role === "user" && canAttach) {
      void attachOnOpen(conversationId);
    } else if (last.role === "assistant" && last.status === "running") {
      await settleCloudRunningAssistant(conversationId, recovery);
    } else {
      // Warm reopen may leave a mid-slice empty incomplete from a prior preempt.
      settleOrphanEmptyAssistants(conversationId);
    }
  }
  return "cloud";
}
