/**
 * Main-process outbox writebacker (as-built: 双模式工作区 §10.3 / §10.4; 前端技术 §7.2).
 *
 * Reads sidecar outbox JSON under `<userData>/sidecar/outbox/`, POSTs ready
 * records to `/v1/conversations/{id}/local-turns` via pure Bearer, deletes on
 * cloud ack, and pushes reconcile payloads to the renderer.
 *
 * Pause/outbox split (as-built: 双模式工作区 §10.4): pause frames live under
 * `…/paused/` and are handled by SidecarManager — this module only processes
 * outbox. Shared scan entry: {@link recoverLocalPersistence}.
 *
 * 实现已按职责拆到 `./outbox/`：strategy → drain → unsynced。
 * 本文件保持历史 import 路径稳定（`index.ts` / sidecar / 单测仍可从这里取公开符号）。
 */

export type { OutboxRecord } from "./outbox";
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
  shouldSalvageOpenRecord,
  toRecordTurnBody,
  drainOutbox,
  statusSnapshot,
  listUnsyncedSummaries,
  flushTurn,
  recoverLocalPersistence,
  handleOccupiedTurnSidecarFailure,
  startOutboxPolling,
  stopOutboxPolling,
  registerOutboxIpc,
  occupyLocalTurnBegin,
  abortLocalTurnPlaceholder,
  checkpointOpenRecord,
  markLocalTurnSettled,
  noteOccupiedLocalTurn,
  resetLocalTurnProjectionForTests,
  startOutboxProjectionWatch,
  stopOutboxProjectionWatch,
  withUmidLock,
} from "./outbox";
