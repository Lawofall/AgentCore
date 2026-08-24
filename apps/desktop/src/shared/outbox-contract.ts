/**
 * Outbox writeback IPC contract — main ↔ renderer (as-built: 前端技术 §四 / §7.2).
 *
 * Main process drains sidecar outbox files via Bearer POST `/local-turns`.
 * Renderer only displays + reflects sync state; reconcile arrives on `synced`.
 * `synced_pending` is a local UI hint (as-built: 前端 UX §一B) — not a cross-platform event.
 */

/** Cloud ack for one local turn (mirrors RecordTurnResponse). */
export interface OutboxSyncedPayload {
  conversationId: string;
  userMessageId: string;
  /** Cloud-authoritative user message id (usually same as optimistic). */
  cloudUserMessageId: string;
  assistantMessageId: string | null;
  title: string | null;
  /** RecordTurnRequest.origin — harvest write-back stamps execution_harvest. */
  origin?: string | null;
  /** RecordTurnRequest.harvest_kind when the sealed row was a closing turn. */
  harvestKind?: string | null;
}

/** Pending / failed outbox row for status IPC. */
export interface OutboxPendingEntry {
  userMessageId: string;
  conversationId: string;
  phase: "open" | "ready";
  updatedAt: number;
}

export interface OutboxStatusSnapshot {
  pending: OutboxPendingEntry[];
}

export interface OutboxFlushTurnRequest {
  userMessageId: string;
}

export interface OutboxFlushTurnResult {
  ok: boolean;
  synced?: OutboxSyncedPayload;
  error?: string;
}

export const OUTBOX_CHANNELS = {
  flush: "outbox:flush",
  flushTurn: "outbox:flushTurn",
  status: "outbox:status",
  synced: "outbox:synced",
  /** Cross-process refresh single-flight (as-built: 认证与会话 §五 / §七) — renderer tryRefresh delegates here. */
  authRefresh: "outbox:authRefresh",
  /** Stamp HTTP login cookies with expirationDate and flush sqlite (reopen stay-logged-in). */
  persistAuthCookies: "outbox:persistAuthCookies",
} as const;

/**
 * Silent token refresh outcome (main ↔ renderer).
 * - `renewed` — new tokens written; caller may replay / reconnect
 * - `auth_dead` — session is gone (401/403 / missing refresh); drop to login
 * - `transient` — network / 5xx / cookie-write failure; never treat as logout
 */
export type AuthRefreshResult = "renewed" | "auth_dead" | "transient";

export interface OutboxApi {
  /** Drain all ready outbox records (exit / manual). */
  flush(): Promise<OutboxStatusSnapshot>;
  /** Drain until this turn is synced (or fail). */
  flushTurn(req: OutboxFlushTurnRequest): Promise<OutboxFlushTurnResult>;
  /** Snapshot of pending outbox rows. */
  status(): Promise<OutboxStatusSnapshot>;
  /** Subscribe to successful cloud acks (reconcile optimistic bubbles). */
  onSynced(cb: (payload: OutboxSyncedPayload) => void): () => void;
  /** Main-owned token refresh (single-flight across renderer + writebacker). */
  authRefresh(): Promise<AuthRefreshResult>;
  /** Re-stamp auth cookies as persistent + flush to disk (Electron). No-op if jar empty. */
  persistAuthCookies(): Promise<void>;
}
