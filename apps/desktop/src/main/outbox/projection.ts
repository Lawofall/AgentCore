/**
 * Main-process local-turn projection writer.
 *
 * Occupy (begin) + mid-turn journal/segments + abort. READY finalize still
 * belongs to {@link drainOutbox} → POST ``.../local-turns``.
 */
import { type FSWatcher, watch } from "node:fs";
import { mkdir } from "node:fs/promises";
import { bearerPostJson } from "../auth-client";
import { logDesktop } from "../log-service";
import {
  type OutboxRecord,
  PHASE_OPEN,
  PHASE_READY,
  isHex32TraceId,
  isSafeOutboxId,
  journalEntriesWithExplicitSeq,
  outboxDir,
  readOutboxRecord,
  streamSegmentsForPost,
} from "./strategy";

export interface LocalTurnOccupyArgs {
  conversationId: string;
  userMessage: string;
  userMessageId: string;
  messageId: string;
  traceId: string;
  agentMentions?: Array<{ agent_id: string; role: string }>;
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
}

export function markLocalTurnSettled(userMessageId: string): void {
  settled.add(userMessageId);
  occupied.delete(userMessageId);
}

export function isLocalTurnOccupied(userMessageId: string): boolean {
  return occupied.has(userMessageId);
}

function localTurnsPath(conversationId: string, suffix: string): string {
  return `/v1/conversations/${encodeURIComponent(conversationId)}/local-turns${suffix}`;
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
  if (mentions.length > 0) body.agent_mentions = mentions;
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
}

async function postOpenCheckpoint(record: OutboxRecord): Promise<void> {
  const conversationId = record.conversation_id;
  const messageId = (record.message_id || "").trim();
  if (!isSafeOutboxId(conversationId) || !messageId) return;

  const journal = journalEntriesWithExplicitSeq(record.journal);
  if (journal.length > 0) {
    try {
      const result = await bearerPostJson(
        localTurnsPath(conversationId, "/journal"),
        {
          message_id: messageId,
          replace: false,
          entries: journal,
        },
      );
      if (!result.ok) {
        logDesktop({
          level: "warn",
          event: "outbox.local_turn_journal_failed",
          fields: {
            conversation_id: conversationId,
            user_message_id: record.user_message_id,
            status: result.status,
          },
        });
      }
    } catch (err) {
      logDesktop({
        level: "warn",
        event: "outbox.local_turn_journal_failed",
        fields: {
          conversation_id: conversationId,
          user_message_id: record.user_message_id,
          error: err instanceof Error ? err.message : String(err),
        },
      });
    }
  }

  const segments = streamSegmentsForPost(record.stream_segments);
  if (segments.length > 0) {
    try {
      const result = await bearerPostJson(
        localTurnsPath(conversationId, "/stream-segments"),
        { message_id: messageId, segments },
      );
      if (!result.ok) {
        logDesktop({
          level: "warn",
          event: "outbox.local_turn_segments_failed",
          fields: {
            conversation_id: conversationId,
            user_message_id: record.user_message_id,
            status: result.status,
          },
        });
      }
    } catch (err) {
      logDesktop({
        level: "warn",
        event: "outbox.local_turn_segments_failed",
        fields: {
          conversation_id: conversationId,
          user_message_id: record.user_message_id,
          error: err instanceof Error ? err.message : String(err),
        },
      });
    }
  }
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

/** Test helper: drop occupy / settle bookkeeping. */
export function resetLocalTurnProjectionForTests(): void {
  occupied.clear();
  settled.clear();
  stopOutboxProjectionWatch();
}
