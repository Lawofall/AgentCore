/**
 * 冷卡服务端终态判据（ask_user / plan_review）。
 *
 * 可点性只认这一份：checkpoint 一旦在服务端结算（journal 已有 live `*_resolved`，
 * 或本会话已观察过该终态），任何路径都不得再把它当待答卡画出来。
 * `reopen` 与 `selectVisibleColdResumes` 共用，禁止在复活路径上各写一道 if。
 */

import {
  idFromResolvedPayload,
  isColdResumeKind,
  kindFromResolvedEvent,
} from "./types";

const notedSettledIds = new Set<string>();

type JournalEvent = { type?: string; payload?: unknown };

let journalReader: ((conversationId: string) => ReadonlySet<string>) | null =
  null;

/** resume.ts 注册：从会话消息 journal 读已结算 id（store 不反向依赖 conversation）。 */
export function registerColdJournalReader(
  reader: ((conversationId: string) => ReadonlySet<string>) | null,
): void {
  journalReader = reader;
}

export function noteColdServerSettled(id: string): void {
  if (id) notedSettledIds.add(id);
}

export function forgetColdServerSettled(id: string): void {
  notedSettledIds.delete(id);
}

export function clearColdServerSettled(): void {
  notedSettledIds.clear();
}

export function isNotedColdServerSettled(id: string): boolean {
  return notedSettledIds.has(id);
}

export function journalSettledIdsFor(
  conversationId: string,
): ReadonlySet<string> {
  return journalReader?.(conversationId) ?? new Set();
}

export function checkpointIdIfColdResolved(
  type: string,
  payload: unknown,
): string | null {
  const kind = kindFromResolvedEvent(type);
  if (!kind || !isColdResumeKind(kind)) return null;
  return idFromResolvedPayload(
    kind,
    (payload ?? {}) as Record<string, unknown>,
  );
}

export function settledColdIdsFromEvents(
  events: ReadonlyArray<JournalEvent> | undefined,
): Set<string> {
  const ids = new Set<string>();
  if (!events) return ids;
  for (const ev of events) {
    if (typeof ev.type !== "string") continue;
    const id = checkpointIdIfColdResolved(ev.type, ev.payload);
    if (id) ids.add(id);
  }
  return ids;
}

export function collectMessageJournalEvents(
  messages: ReadonlyArray<{
    runs?: { events?: ReadonlyArray<JournalEvent> };
  }>,
): JournalEvent[] {
  const out: JournalEvent[] = [];
  for (const m of messages) {
    const events = m.runs?.events;
    if (events) out.push(...events);
  }
  return out;
}

export function isColdCheckpointSettled(input: {
  checkpointId: string;
  entry?: {
    status?: string;
    resumeSettled?: unknown;
    settledByReceipt?: boolean;
  };
  conversationId?: string;
  journalSettledIds?: ReadonlySet<string>;
}): boolean {
  const { checkpointId, entry } = input;
  if (!checkpointId) return false;
  if (isNotedColdServerSettled(checkpointId)) return true;
  if (entry?.status === "resolved" || entry?.status === "orphaned") return true;
  if (entry?.resumeSettled || entry?.settledByReceipt) return true;
  const journalIds =
    input.journalSettledIds ??
    (input.conversationId
      ? journalSettledIdsFor(input.conversationId)
      : undefined);
  return journalIds?.has(checkpointId) === true;
}
