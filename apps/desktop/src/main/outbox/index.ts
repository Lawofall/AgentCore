/**
 * main/outbox 桶出口。
 *
 * 拆轴：strategy（纯策略/路径/记录 IO）→ drain（循环/flush/IPC）→
 * unsynced（dead-letter / 未同步摘要）。
 * `../outbox-writeback.ts` 保持历史 import 路径稳定。
 */

export type { OutboxRecord } from "./strategy";
export {
  isSafeOutboxId,
  sidecarDataDir,
  outboxDir,
  pausedDir,
  deadLetterDir,
  isPermanentHttpFailure,
  computeBackoffDelayMs,
  normalizeToolFailureCode,
  toolFailuresFromJournal,
  EMPTY_USER_MESSAGE_PLACEHOLDER,
  canPostEmptyUserMessage,
  fillEmptyUserMessageForWriteback,
  fillFromCaptainStreamSegments,
  recordHasProcessState,
  shouldDeleteOutboxAfterAck,
  toRecordTurnBody,
} from "./strategy";

export { listUnsyncedSummaries } from "./unsynced";

export {
  drainOutbox,
  statusSnapshot,
  flushTurn,
  recoverLocalPersistence,
  startOutboxPolling,
  stopOutboxPolling,
  registerOutboxIpc,
} from "./drain";

export {
  occupyLocalTurnBegin,
  abortLocalTurnPlaceholder,
  checkpointOpenRecord,
  markLocalTurnSettled,
  noteOccupiedLocalTurn,
  resetLocalTurnProjectionForTests,
  startOutboxProjectionWatch,
  stopOutboxProjectionWatch,
  withUmidLock,
} from "./projection";
