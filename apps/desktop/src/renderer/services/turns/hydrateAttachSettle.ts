/**
 * Open-time attach/settle after message-window fetch (P4 unified hydrate).
 *
 * Decoupled from message-window adopt: ConversationPage reveals immediately when
 * the slice already has content (or list metadata confirms messageCount===0); on an
 * empty cold slice it awaits {@link awaitHydrateAttachSettle} before reveal so
 * unsynced projection does not flash a white screen. Warm reopen with content still
 * schedules settle in the background via {@link scheduleHydrateAttachSettle}.
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

/**
 * Await recovery then project unsynced / kick attach.
 * Does **not** wait for a live sidecar turn to finish — overlay may reveal
 * once history is projected; attach continues in the background.
 * Safe when `loadRecovery` never rejects.
 */
export async function awaitHydrateAttachSettle(
  conversationId: string,
  recoveryLoaded: Promise<ConversationRecovery>,
): Promise<"local" | "cloud" | undefined> {
  const recovery = await recoveryLoaded;
  if (
    useConversationStore.getState().currentConversationId !== conversationId
  ) {
    return;
  }
  return runHydrateAttachSettle(conversationId, recovery, {
    waitForAttach: false,
  });
}

/** Kick attach/settle when recovery lands. Does not delay overlay reveal. */
export function scheduleHydrateAttachSettle(
  conversationId: string,
  recoveryLoaded: Promise<ConversationRecovery>,
): void {
  void awaitHydrateAttachSettle(conversationId, recoveryLoaded);
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
  opts?: { waitForAttach?: boolean },
): Promise<"local" | "cloud"> {
  const waitForAttach = opts?.waitForAttach !== false;
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
  // 本机 sidecar 活着不拆 slot：本端连接闸静音，回合结束后同一条 follow 接着收。
  // 迟到的 hydrate 不抢订：已切走则不动全局那一条。
  // 卸订 ≠ 用户切走：follow_closed.reason 必须是卸订因由，禁止冒充 switched_away。
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
    // 揭窗可能已经订了 follow：先占闸静音，避免 sidecar 快照与跟播 dual-fold。
    // schedule 路径 hydrate 立刻返回，闸必须跟 attach 同寿，不能在这里 finally 放掉。
    const occupyUntilAttach =
      recovery.sidecarLive && recovery.pausedCount === 0;
    const releaseSidecarYield = occupyUntilAttach
      ? beginLocalConversationStream(conversationId)
      : null;
    let yieldHeldAcrossReturn = false;
    try {
      projectUnsyncedTurns(conversationId, recovery.unsynced);
      // Paused local turns skip attach (no live buffer). Cloud pause writeback
      // omits turn_journal, so reinject display runs from the pause frame.
      if (recovery.pausedCount > 0) {
        projectPausedRuns(conversationId, recovery.pausedRuns ?? {});
      }
      // After unsynced project: seal any blank open/ghost assistants as「已中断」.
      settleOrphanEmptyAssistants(conversationId);
      if (occupyUntilAttach) {
        // 切会话不卸观察泵 — 无页级 signal。
        const attached = attachSidecarTurn(conversationId);
        if (waitForAttach) {
          await attached;
        } else {
          yieldHeldAcrossReturn = true;
          void attached.finally(() => {
            releaseSidecarYield?.();
          });
        }
      }
      return "local";
    } finally {
      if (!yieldHeldAcrossReturn) releaseSidecarYield?.();
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
