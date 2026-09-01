/**
 * Main-process local-turn projection writer.
 *
 * Occupy (begin) + mid-turn journal/segments + abort. READY finalize still
 * belongs to {@link drainOutbox} → POST ``.../local-turns``.
 */
import { type FSWatcher, watch } from "node:fs";
import { mkdir } from "node:fs/promises";
import type { SidecarQueuedAttachment } from "@shared/sidecar-contract";
import { bearerPostJson } from "../auth-client";
import { logDesktop } from "../log-service";
import {
  JOURNAL_OVERFLOW_SEQ_START,
  type OutboxRecord,
  PHASE_OPEN,
  PHASE_READY,
  computeBackoffDelayMs,
  isHex32TraceId,
  isPermanentHttpFailure,
  isSafeOutboxId,
  journalAckAfterPost,
  journalEntriesWithExplicitSeq,
  outboxDir,
  readOutboxRecord,
  streamSegmentsForPost,
  unackedJournalEntries,
} from "./strategy";

export interface LocalTurnOccupyArgs {
  conversationId: string;
  userMessage: string;
  userMessageId: string;
  messageId: string;
  traceId: string;
  agentMentions?: Array<{ agent_id: string; role: string }>;
  regenerate?: boolean;
  attachments?: SidecarQueuedAttachment[];
}

export interface LocalTurnAbortArgs {
  conversationId: string;
  userMessageId: string;
  messageId: string;
}

type OccupiedMeta = {
  conversationId: string;
  messageId: string;
};

const occupied = new Map<string, OccupiedMeta>();
const settled = new Set<string>();
const umidLocks = new Map<string, Promise<unknown>>();

let watchHandle: FSWatcher | null = null;
const watchTimers = new Map<string, ReturnType<typeof setTimeout>>();

/** In-memory outbox sync cursor (never write backoff onto the sidecar-owned OPEN file). */
type OpenSyncState = {
  ackedLiveSeq: number;
  ackedOverflowSeq: number;
  lastSegmentsKey: string | null;
  backoffUntil: number;
  failCount: number;
  loggedFail: boolean;
  retryTimer: ReturnType<typeof setTimeout> | null;
};

const openSync = new Map<string, OpenSyncState>();

function emptyOpenSync(): OpenSyncState {
  return {
    ackedLiveSeq: -1,
    ackedOverflowSeq: JOURNAL_OVERFLOW_SEQ_START - 1,
    lastSegmentsKey: null,
    backoffUntil: 0,
    failCount: 0,
    loggedFail: false,
    retryTimer: null,
  };
}

function openSyncFor(userMessageId: string): OpenSyncState {
  let st = openSync.get(userMessageId);
  if (!st) {
    st = emptyOpenSync();
    openSync.set(userMessageId, st);
  }
  return st;
}

function clearOpenSync(userMessageId: string): void {
  const st = openSync.get(userMessageId);
  if (st?.retryTimer) clearTimeout(st.retryTimer);
  openSync.delete(userMessageId);
}

function clearAllOpenSync(): void {
  for (const st of openSync.values()) {
    if (st.retryTimer) clearTimeout(st.retryTimer);
  }
  openSync.clear();
}

function segmentsFingerprint(
  rows: Array<{ channel: string; text: string; generation: number }>,
): string {
  const ordered = [...rows].sort((a, b) => a.channel.localeCompare(b.channel));
  return JSON.stringify(ordered.map((r) => [r.channel, r.text, r.generation]));
}

function scheduleOpenRetry(userMessageId: string, delayMs: number): void {
  const st = openSyncFor(userMessageId);
  if (st.retryTimer) clearTimeout(st.retryTimer);
  st.retryTimer = setTimeout(
    () => {
      st.retryTimer = null;
      void withUmidLock(userMessageId, async () => {
        const record = await readOutboxRecord(userMessageId);
        if (!record || record.phase !== PHASE_OPEN) {
          clearOpenSync(userMessageId);
          return;
        }
        await postOpenCheckpoint(record);
      });
    },
    Math.max(0, delayMs),
  );
}

export function withUmidLock<T>(
  userMessageId: string,
  fn: () => Promise<T>,
): Promise<T> {
  const prev = umidLocks.get(userMessageId) ?? Promise.resolve();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const chained = prev.then(
    () => gate,
    () => gate,
  );
  umidLocks.set(userMessageId, chained);
  return prev
    .then(
      () => fn(),
      () => fn(),
    )
    .finally(() => {
      release();
      if (umidLocks.get(userMessageId) === chained) {
        umidLocks.delete(userMessageId);
      }
    });
}

export function noteOccupiedLocalTurn(
  userMessageId: string,
  meta: OccupiedMeta,
): void {
  occupied.set(userMessageId, meta);
  settled.delete(userMessageId);
  clearOpenSync(userMessageId);
  syncLocalTurnLeaseHeartbeatLoop();
}

export function markLocalTurnSettled(userMessageId: string): void {
  settled.add(userMessageId);
  occupied.delete(userMessageId);
  clearOpenSync(userMessageId);
  syncLocalTurnLeaseHeartbeatLoop();
}

export function isLocalTurnOccupied(userMessageId: string): boolean {
  return occupied.has(userMessageId);
}

function localTurnsPath(conversationId: string, suffix: string): string {
  return `/v1/conversations/${encodeURIComponent(conversationId)}/local-turns${suffix}`;
}

/** Matches server ``turn_lease_heartbeat_seconds``. TTL is 90s. */
export const LOCAL_TURN_LEASE_HEARTBEAT_MS = 20_000;

let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

function stopLocalTurnLeaseHeartbeatLoop(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function syncLocalTurnLeaseHeartbeatLoop(): void {
  if (occupied.size === 0) {
    stopLocalTurnLeaseHeartbeatLoop();
    return;
  }
  if (heartbeatTimer) return;
  heartbeatTimer = setInterval(() => {
    void heartbeatOccupiedLocalTurns();
  }, LOCAL_TURN_LEASE_HEARTBEAT_MS);
}

async function heartbeatOccupiedLocalTurns(): Promise<void> {
  const snapshots = [...occupied.entries()];
  for (const [userMessageId, meta] of snapshots) {
    if (!occupied.has(userMessageId)) continue;
    try {
      const result = await bearerPostJson(
        localTurnsPath(meta.conversationId, "/heartbeat"),
        { message_id: meta.messageId },
      );
      if (!result.ok) {
        logDesktop({
          level: "warn",
          event: "outbox.local_turn_heartbeat_failed",
          fields: {
            conversation_id: meta.conversationId,
            user_message_id: userMessageId,
            status: result.status,
          },
        });
      }
    } catch (err) {
      logDesktop({
        level: "warn",
        event: "outbox.local_turn_heartbeat_failed",
        fields: {
          conversation_id: meta.conversationId,
          user_message_id: userMessageId,
          error: err instanceof Error ? err.message : String(err),
        },
      });
    }
  }
}

/**
 * POST begin (user + running assistant). Idempotent on the same message_id.
 * Returns false on HTTP / network failure — caller must not start the engine.
 */
export async function occupyLocalTurnBegin(
  args: LocalTurnOccupyArgs,
): Promise<boolean> {
  const conversationId = args.conversationId.trim();
  const userMessageId = args.userMessageId.trim();
  const messageId = args.messageId.trim();
  const traceId = args.traceId.trim();
  if (
    !isSafeOutboxId(conversationId) ||
    !isSafeOutboxId(userMessageId) ||
    !messageId ||
    !isHex32TraceId(traceId)
  ) {
    logDesktop({
      level: "error",
      event: "outbox.local_turn_begin_invalid",
      fields: {
        conversation_id: conversationId,
        user_message_id: userMessageId,
      },
    });
    return false;
  }
  const mentionsDefined = args.agentMentions !== undefined;
  const mentions = (args.agentMentions || []).filter(
    (m) =>
      m &&
      typeof m.agent_id === "string" &&
      m.agent_id.trim() &&
      typeof m.role === "string" &&
      m.role.trim(),
  );
  const body: Record<string, unknown> = {
    user_message: args.userMessage,
    user_message_id: userMessageId,
    message_id: messageId,
    trace_id: traceId,
  };
  if (args.regenerate) body.regenerate = true;
  if (mentions.length > 0 || (args.regenerate && mentionsDefined)) {
    body.agent_mentions = mentions;
  }
  if (args.attachments !== undefined) {
    body.attachments = args.attachments;
  }
  let result: { ok: boolean; status: number; body: unknown };
  try {
    result = await bearerPostJson(
      localTurnsPath(conversationId, "/begin"),
      body,
    );
  } catch (err) {
    logDesktop({
      level: "error",
      event: "outbox.local_turn_begin_failed",
      fields: {
        conversation_id: conversationId,
        user_message_id: userMessageId,
        error: err instanceof Error ? err.message : String(err),
      },
    });
    return false;
  }
  if (!result.ok) {
    logDesktop({
      level: "error",
      event: "outbox.local_turn_begin_failed",
      fields: {
        conversation_id: conversationId,
        user_message_id: userMessageId,
        status: result.status,
      },
    });
    return false;
  }
  noteOccupiedLocalTurn(userMessageId, { conversationId, messageId });
  return true;
}

/** Roll back a still-running cloud placeholder. No-op when already settled. */
export async function abortLocalTurnPlaceholder(
  args: LocalTurnAbortArgs,
): Promise<void> {
  const userMessageId = args.userMessageId.trim();
  const conversationId = args.conversationId.trim();
  const messageId = args.messageId.trim();
  if (!isSafeOutboxId(conversationId) || !isSafeOutboxId(userMessageId)) return;
  if (settled.has(userMessageId)) return;
  const onDisk = await readOutboxRecord(userMessageId);
  if (onDisk?.phase === PHASE_READY) return;
  try {
    await bearerPostJson(localTurnsPath(conversationId, "/abort"), {
      user_message_id: userMessageId,
      message_id: messageId,
    });
  } catch (err) {
    logDesktop({
      level: "error",
      event: "outbox.local_turn_abort_failed",
      fields: {
        conversation_id: conversationId,
        user_message_id: userMessageId,
        error: err instanceof Error ? err.message : String(err),
      },
    });
    return;
  }
  occupied.delete(userMessageId);
  clearOpenSync(userMessageId);
  syncLocalTurnLeaseHeartbeatLoop();
}

async function postOpenCheckpoint(record: OutboxRecord): Promise<void> {
  const conversationId = record.conversation_id;
  const messageId = (record.message_id || "").trim();
  const umid = record.user_message_id;
  if (!isSafeOutboxId(conversationId) || !messageId) return;

  const st = openSyncFor(umid);
  const now = Date.now();
  if (now < st.backoffUntil) {
    scheduleOpenRetry(umid, st.backoffUntil - now);
    return;
  }

  const allJournal = journalEntriesWithExplicitSeq(record.journal);
  const journal = unackedJournalEntries(
    allJournal,
    st.ackedLiveSeq,
    st.ackedOverflowSeq,
  );
  const segments = streamSegmentsForPost(record.stream_segments);
  const segKey = segmentsFingerprint(segments);
  const needJournal = journal.length > 0;
  const needSegments = segments.length > 0 && segKey !== st.lastSegmentsKey;
  if (!needJournal && !needSegments) return;

  let failed = false;

  if (needJournal) {
    try {
      const result = await bearerPostJson(
        localTurnsPath(conversationId, "/journal"),
        {
          message_id: messageId,
          replace: false,
          entries: journal,
        },
      );
      if (result.ok) {
        const next = journalAckAfterPost(
          journal,
          st.ackedLiveSeq,
          st.ackedOverflowSeq,
        );
        st.ackedLiveSeq = next.ackedLiveSeq;
        st.ackedOverflowSeq = next.ackedOverflowSeq;
      } else {
        failed = true;
        logCheckpointFail(st, "outbox.local_turn_journal_failed", {
          conversation_id: conversationId,
          user_message_id: umid,
          status: result.status,
          permanent: isPermanentHttpFailure(result.status),
        });
      }
    } catch (err) {
      failed = true;
      logCheckpointFail(st, "outbox.local_turn_journal_failed", {
        conversation_id: conversationId,
        user_message_id: umid,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  if (needSegments) {
    try {
      const result = await bearerPostJson(
        localTurnsPath(conversationId, "/stream-segments"),
        { message_id: messageId, segments },
      );
      if (result.ok) {
        st.lastSegmentsKey = segKey;
      } else {
        failed = true;
        logCheckpointFail(st, "outbox.local_turn_segments_failed", {
          conversation_id: conversationId,
          user_message_id: umid,
          status: result.status,
          permanent: isPermanentHttpFailure(result.status),
        });
      }
    } catch (err) {
      failed = true;
      logCheckpointFail(st, "outbox.local_turn_segments_failed", {
        conversation_id: conversationId,
        user_message_id: umid,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  if (!failed) {
    st.failCount = 0;
    st.loggedFail = false;
    st.backoffUntil = 0;
    if (st.retryTimer) {
      clearTimeout(st.retryTimer);
      st.retryTimer = null;
    }
    return;
  }

  st.failCount += 1;
  const delay = computeBackoffDelayMs(st.failCount);
  st.backoffUntil = Date.now() + delay;
  scheduleOpenRetry(umid, delay);
}

function logCheckpointFail(
  st: OpenSyncState,
  event:
    | "outbox.local_turn_journal_failed"
    | "outbox.local_turn_segments_failed",
  fields: Record<string, unknown>,
): void {
  const repeat = st.loggedFail;
  st.loggedFail = true;
  logDesktop({
    level: repeat ? "debug" : "warn",
    event,
    fields: { ...fields, repeat },
  });
}

/**
 * OPEN row: mid-turn journal/segments. Occupy is startTurn-only
 * (user send and FIFO ``queue/needStart``). Never POST local-turns.
 * Caller must hold {@link withUmidLock} for this umid.
 */
export async function checkpointOpenRecord(
  record: OutboxRecord,
): Promise<void> {
  if (record.phase !== PHASE_OPEN) return;
  await postOpenCheckpoint(record);
}

function scheduleWatch(userMessageId: string): void {
  const prev = watchTimers.get(userMessageId);
  if (prev) clearTimeout(prev);
  watchTimers.set(
    userMessageId,
    setTimeout(() => {
      watchTimers.delete(userMessageId);
      void withUmidLock(userMessageId, async () => {
        const record = await readOutboxRecord(userMessageId);
        if (!record) return;
        if (record.phase === PHASE_OPEN) await checkpointOpenRecord(record);
      });
    }, 50),
  );
}

export function startOutboxProjectionWatch(): void {
  if (watchHandle) return;
  const dir = outboxDir();
  void mkdir(dir, { recursive: true })
    .then(() => {
      if (watchHandle) return;
      try {
        watchHandle = watch(dir, { persistent: false }, (_event, filename) => {
          const name = typeof filename === "string" ? filename : "";
          if (!name.endsWith(".json") || name.includes(".tmp")) return;
          const umid = name.slice(0, -".json".length);
          if (!isSafeOutboxId(umid)) return;
          scheduleWatch(umid);
        });
      } catch (err) {
        logDesktop({
          level: "warn",
          event: "outbox.projection_watch_failed",
          fields: { error: err instanceof Error ? err.message : String(err) },
        });
      }
    })
    .catch(() => undefined);
}

export function stopOutboxProjectionWatch(): void {
  if (watchHandle) {
    watchHandle.close();
    watchHandle = null;
  }
  for (const timer of watchTimers.values()) clearTimeout(timer);
  watchTimers.clear();
}

/** Test helper: drop occupy / settle / mid-turn sync bookkeeping. */
export function resetLocalTurnProjectionForTests(): void {
  occupied.clear();
  settled.clear();
  clearAllOpenSync();
  stopLocalTurnLeaseHeartbeatLoop();
  stopOutboxProjectionWatch();
}
