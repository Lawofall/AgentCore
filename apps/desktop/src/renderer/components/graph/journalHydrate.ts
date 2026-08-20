import type { ExecutionJournal } from "@/stores/execution";

/** Single-turn identity (TurnDetailPage / InlineTeamGraph). */
export function journalHydrateIdentity(
  journal: ExecutionJournal | undefined | null,
): { journal: ExecutionJournal; events: number } | null {
  if (!journal) return null;
  return { journal, events: journal.events.length };
}

export function journalHydrateIdentityEqual(
  a: { journal: ExecutionJournal; events: number } | null,
  b: { journal: ExecutionJournal; events: number } | null,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.journal === b.journal && a.events === b.events;
}
