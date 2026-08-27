/**
 * Outbox reconcile + exit flush (as-built: 双模式工作区 §10.3；前端技术 §7.2)。
 *
 * Main process owns cloud writeback; renderer reflects sync acks (`synced_pending`
 * → `synced`) and flushes pending outbox on window close / app quit.
 *
 * Historical harvest write-backs no longer mint a sibling bubble; hide leftover
 * rows on read. Do not extra-refresh the window on those acks.
 */
import { patchConversationCache } from "@/hooks/useConversations";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import type { OutboxSyncedPayload } from "@shared/outbox-contract";

const SYNCED_HINT_MS = 2500;

/** Reconcile one successful drain ack. Exported for write-back tests. */
export function applyOutboxSynced(payload: OutboxSyncedPayload): void {
  const { conversationId, userMessageId, cloudUserMessageId, title } = payload;
  if (!conversationId) return;
  const store = useConversationStore.getState();
  try {
    const messages = getRuntime(conversationId).messages;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (lastUser?.id === userMessageId) {
      store.reconcileLastTurn(
        cloudUserMessageId || userMessageId,
        conversationId,
      );
    }
    // Flip local hint: pending → brief "已同步", then clear.
    const anchor = cloudUserMessageId || userMessageId;
    store.setTurnSyncStatus(anchor, "synced", conversationId);
    // Also clear if still keyed by optimistic id (reconcile may have been no-op).
    if (anchor !== userMessageId) {
      store.setTurnSyncStatus(userMessageId, "synced", conversationId);
    }
    setTimeout(() => {
      store.setTurnSyncStatus(anchor, undefined, conversationId);
      if (anchor !== userMessageId) {
        store.setTurnSyncStatus(userMessageId, undefined, conversationId);
      }
    }, SYNCED_HINT_MS);
  } catch {
    // Conversation slice may be unloaded after refresh — cloud reload is authoritative.
  }
  if (title) {
    patchConversationCache(conversationId, { title });
  }
}

/** Re-apply synced_pending from main-process outbox status (reload / reopen). */
async function hydratePendingFromStatus(): Promise<void> {
  if (!window.outboxApi?.status) return;
  try {
    const snap = await window.outboxApi.status();
    const store = useConversationStore.getState();
    for (const row of snap.pending) {
      store.setTurnSyncStatus(
        row.userMessageId,
        "synced_pending",
        row.conversationId,
      );
    }
  } catch {
    // Status is advisory; polling + onSynced remain authoritative.
  }
}

let started = false;

/** Subscribe to main-process sync acks + flush outbox on unload. Idempotent. */
export function startOutboxReconcile(): void {
  if (started || typeof window === "undefined" || !window.outboxApi) return;
  started = true;

  window.outboxApi.onSynced(applyOutboxSynced);
  void hydratePendingFromStatus();

  const flush = () => {
    void window.outboxApi?.flush();
  };
  window.addEventListener("beforeunload", flush);
  // Electron may kill the renderer without beforeunload in some quit paths;
  // pagehide is the broader signal.
  window.addEventListener("pagehide", flush);
}
