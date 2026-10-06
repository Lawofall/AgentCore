import { queryClient } from "@/lib/queryClient";
import { conversationKeys } from "@/lib/queryKeys";
import { useSyncExternalStore } from "react";

/** Sidebar row fields this cut reads and writes. The cache row is the full conversation. */
export interface CutConversationRow {
  id: string;
  contextCompacted?: boolean;
  compactedThrough?: string | null;
  contextCutUndoable?: boolean;
}

const EMPTY_ROWS: CutConversationRow[] = [];

export function useCutConversationRows(): CutConversationRow[] {
  return useSyncExternalStore(
    (onChange) => queryClient.getQueryCache().subscribe(onChange),
    () =>
      queryClient.getQueryData<{ conversations?: CutConversationRow[] }>(
        conversationKeys.grouped,
      )?.conversations ?? EMPTY_ROWS,
  );
}

export function patchContextCutCache(
  id: string,
  patch: Pick<
    CutConversationRow,
    "contextCompacted" | "compactedThrough" | "contextCutUndoable"
  >,
): void {
  queryClient.setQueryData<{
    folders: unknown[];
    conversations: CutConversationRow[];
  }>(conversationKeys.grouped, (old) => {
    if (!old) return old;
    return {
      ...old,
      conversations: old.conversations.map((row) =>
        row.id === id ? { ...row, ...patch } : row,
      ),
    };
  });
}

export interface CutMessageRef {
  id: string;
  role: string;
  createdAt: string;
  status?: "running" | "complete" | "incomplete" | "failed" | null;
}

/**
 * The button exists when something still in the model window sits before the
 * verbatim tail. An assistant message keeps the user line that opened it.
 */
export function contextCutAvailable(input: {
  message: CutMessageRef;
  messages: readonly CutMessageRef[];
  compactedThrough?: string | null;
  hasMoreBefore: boolean;
  blocked: boolean;
}): boolean {
  if (input.blocked) return false;
  if (input.message.status === "running") return false;
  if (input.message.role !== "user" && input.message.role !== "assistant") {
    return false;
  }
  const watermark = input.compactedThrough
    ? Date.parse(input.compactedThrough)
    : Number.NaN;
  const hasWatermark = Number.isFinite(watermark);
  const chronological = [...input.messages].sort(
    (a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt),
  );
  const idx = chronological.findIndex((row) => row.id === input.message.id);
  if (idx < 0) return false;
  let verbatimIndex = idx;
  if (input.message.role === "assistant") {
    verbatimIndex = -1;
    for (let i = idx - 1; i >= 0; i -= 1) {
      if (chronological[i]?.role === "user") {
        verbatimIndex = i;
        break;
      }
    }
    if (verbatimIndex < 0) return false;
  }
  const liveBefore = chronological.slice(0, verbatimIndex).some((row) => {
    const at = Date.parse(row.createdAt);
    return !hasWatermark || at > watermark;
  });
  if (liveBefore) return true;
  if (!input.hasMoreBefore) return false;
  const oldestAt = Date.parse(chronological[0]?.createdAt ?? "");
  return Number.isFinite(oldestAt) && (!hasWatermark || oldestAt > watermark);
}
