import { useConversations } from "@/hooks/useConversations";
import { useFolders } from "@/hooks/useFolders";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  type FolderMeta,
  dedupeFoldersByLocalBinding,
  localFolderBindingKey,
} from "@/services/folders";
import { useRequiredConversationIds } from "@/stores/aiAttention";
import type { Conversation } from "@/stores/conversation";
import { useMemo } from "react";

/** Workspaces (folders) shown in the rail before deferring to /conversations. */
export const MAX_WORKSPACE_GROUPS = 6;

/** Drop local-disk workspaces on runtimes with no local filesystem (web / phone). */
export function foldersForConversationRail(
  folders: FolderMeta[],
): FolderMeta[] {
  return hasLocalFiles() ? folders : folders.filter((f) => f.mode !== "local");
}

/** One sidebar folder group: a folder plus its (recency-sorted) conversations. */
export interface WorkspaceGroup {
  folder: FolderMeta;
  /**
   * Every live conversation in this folder (incl. pinned), newest-first.
   * Header actions (归档全部 / 删文件夹) need the full set; the rail list filters
   * pinned out so they only appear in the 置顶区.
   */
  convs: Conversation[];
  /** Newest `updatedAt` in `convs` (ms epoch), for ordering groups by activity. */
  latest: number;
}

function byRecency(a: Conversation, b: Conversation): number {
  return (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0);
}

/**
 * Partition conversations into folder groups (前端UX §一 方案C): folder → its
 * conversations (newest-first; pinned included for header/latest), groups ordered
 * by latest activity and capped at {@link MAX_WORKSPACE_GROUPS}. Pure (no React)
 * so it's unit-testable; the {@link useWorkspaceGroups} hook just memoizes it.
 *
 * **裸聊 (folderless chats) are excluded** — they live in「快速对话」. Pinned
 * foldered chats stay in `convs` for group actions but the rail renders them only
 * in the 置顶区 (零重复). Conversations whose folder isn't in `folders` (e.g.
 * mid-deletion) are skipped; the delete flow unbinds them to 裸聊 so they
 * resurface in「快速对话」.
 *
 * required 不另开「等你」栏：所在组挤进 ≤6（顶掉最不活跃且非 required 的组）。
 * 组内回塞 / 折组覆盖在 {@link WorkspaceGroups}；归档 / 置顶 required 不为此挤组。
 *
 * Map each folder id → the canonical (first / oldest) id for its local binding
 * lives in {@link canonicalFolderIds}: cloud folders map to themselves so
 * sidebar groups don't duplicate the same local path when historical duplicate
 * rows exist.
 */
function canonicalFolderIds(folders: FolderMeta[]): Map<string, string> {
  const keptByBinding = new Map<string, string>();
  const canonical = new Map<string, string>();
  for (const f of folders) {
    if (f.mode === "local" && f.localRootId) {
      const key = localFolderBindingKey(f.localRootId, f.localSubpath);
      const kept = keptByBinding.get(key);
      if (kept) {
        canonical.set(f.id, kept);
      } else {
        keptByBinding.set(key, f.id);
        canonical.set(f.id, f.id);
      }
    } else {
      canonical.set(f.id, f.id);
    }
  }
  return canonical;
}

function groupHasUnpinnedRequired(
  group: WorkspaceGroup,
  requiredIds: ReadonlySet<string>,
): boolean {
  return group.convs.some(
    (c) => !c.pinned && !c.archived && requiredIds.has(c.id),
  );
}

/** Recency-capped rail groups; overflow required groups squeeze in, still ≤6. */
export function pickVisibleWorkspaceGroups(
  groups: WorkspaceGroup[],
  requiredIds: ReadonlySet<string>,
): WorkspaceGroup[] {
  if (groups.length <= MAX_WORKSPACE_GROUPS) return groups;
  const top = groups.slice(0, MAX_WORKSPACE_GROUPS);
  const required = groups.filter((g) =>
    groupHasUnpinnedRequired(g, requiredIds),
  );
  if (required.length === 0) return top;
  const topIds = new Set(top.map((g) => g.folder.id));
  if (required.every((g) => topIds.has(g.folder.id))) return top;
  const takeRequired = required.slice(0, MAX_WORKSPACE_GROUPS);
  const taken = new Set(takeRequired.map((g) => g.folder.id));
  const others = groups
    .filter((g) => !taken.has(g.folder.id))
    .slice(0, MAX_WORKSPACE_GROUPS - takeRequired.length);
  const keep = new Set([...takeRequired, ...others].map((g) => g.folder.id));
  return groups.filter((g) => keep.has(g.folder.id));
}

export function buildWorkspaceGroups(
  conversations: Conversation[],
  folders: FolderMeta[],
  requiredIds: ReadonlySet<string> = new Set(),
  opts?: { uncapped?: boolean },
): WorkspaceGroup[] {
  const displayFolders = dedupeFoldersByLocalBinding(folders);
  const canonical = canonicalFolderIds(folders);
  const byFolder = new Map<string, Conversation[]>();
  for (const c of conversations) {
    if (!c.folderId) continue; // 裸聊 — belongs to「快速对话」, not a group
    const folderId = canonical.get(c.folderId) ?? c.folderId;
    const arr = byFolder.get(folderId);
    if (arr) arr.push(c);
    else byFolder.set(folderId, [c]);
  }
  const folderById = new Map(displayFolders.map((f) => [f.id, f]));
  const result: WorkspaceGroup[] = [];
  for (const [folderId, convs] of byFolder) {
    const folder = folderById.get(folderId);
    if (!folder) continue; // folder not in cache (e.g. just deleted) — skip
    convs.sort(byRecency);
    const latest = convs.reduce(
      (m, c) => Math.max(m, Date.parse(c.updatedAt) || 0),
      0,
    );
    result.push({ folder, convs, latest });
  }
  result.sort((a, b) => b.latest - a.latest);
  if (opts?.uncapped) return result;
  return pickVisibleWorkspaceGroups(result, requiredIds);
}

/**
 * The sidebar's folder groups over the live grouped cache. Shared by
 * `WorkspaceGroups` (renders them) and `RecentConversations` (bare-chat zone)
 * so the partition lives in one place.
 */
export function useWorkspaceGroups(): WorkspaceGroup[] {
  const conversations = useConversations();
  const allFolders = useFolders();
  const folders = foldersForConversationRail(allFolders);
  const requiredIds = useRequiredConversationIds();
  return useMemo(
    () => buildWorkspaceGroups(conversations, folders, requiredIds),
    [conversations, folders, requiredIds],
  );
}
