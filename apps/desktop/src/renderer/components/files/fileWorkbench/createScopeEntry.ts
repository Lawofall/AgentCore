import { queryClient } from "@/lib/queryClient";
import { notifyError } from "@/lib/toast";
import { createRuleDocument, listScopeEntries } from "@/services/documents";
import {
  type EntryOpenTarget,
  type EntryScope,
  entryOpenTarget,
} from "./EntriesSection";

/** Same prefix as {@link EntriesSection}'s list query — keep in lockstep. */
const ENTRIES_QUERY_KEY = ["scope-entries"] as const;

/** Collision-free「新条目.md」within a scope's existing names. */
export function nextEntryName(existing: Iterable<string>): string {
  const taken = new Set(existing);
  const base = "新条目";
  if (!taken.has(`${base}.md`)) return `${base}.md`;
  for (let i = 2; ; i++) {
    const candidate = `${base} ${i}.md`;
    if (!taken.has(candidate)) return candidate;
  }
}

/**
 * Create a user-owned entry in `scope` and open it. Does not require
 * {@link EntriesSection} to be mounted (header「新建」works while collapsed).
 * Returns false when the API call failed (caller should not expand).
 */
export async function createAndOpenScopeEntry(
  scope: EntryScope,
  onOpen: (target: EntryOpenTarget) => void,
): Promise<boolean> {
  const folderId = scope.kind === "folder" ? scope.folderId : null;
  try {
    const rows = await queryClient.fetchQuery({
      queryKey: [...ENTRIES_QUERY_KEY, folderId ?? "global"],
      queryFn: () => listScopeEntries(folderId),
    });
    const doc = await createRuleDocument(
      nextEntryName(rows.map((r) => r.name)),
      folderId,
    );
    await queryClient.invalidateQueries({ queryKey: ENTRIES_QUERY_KEY });
    onOpen(entryOpenTarget(doc));
    return true;
  } catch (e) {
    notifyError(e, "新建条目失败");
    return false;
  }
}
