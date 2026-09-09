import type { FolderMeta } from "@/services/folders";

/** Default folder rows in draft「在哪工作」before「查看全部」. */
export const DRAFT_FOLDER_PREVIEW_LIMIT = 5;

function msOrZero(iso: string): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
}

/** Best-effort「最近活跃」: max conversation `updatedAt` in that folder. */
export function folderActivityMs(
  folderId: string,
  conversations: readonly { folderId?: string | null; updatedAt: string }[],
): number {
  let max = 0;
  for (const c of conversations) {
    if (c.folderId !== folderId) continue;
    max = Math.max(max, msOrZero(c.updatedAt));
  }
  return max;
}

/** Recent-first; name asc as tie-break for stable order. */
export function sortFoldersByRecentActivity(
  folders: FolderMeta[],
  conversations: readonly { folderId?: string | null; updatedAt: string }[],
): FolderMeta[] {
  return [...folders].sort((a, b) => {
    const d =
      folderActivityMs(b.id, conversations) -
      folderActivityMs(a.id, conversations);
    if (d !== 0) return d;
    return a.name.localeCompare(b.name, "zh");
  });
}

export function filterFoldersByName(
  folders: FolderMeta[],
  query: string,
): FolderMeta[] {
  const q = query.trim().toLowerCase();
  if (!q) return folders;
  return folders.filter((f) => f.name.toLowerCase().includes(q));
}

/**
 * Draft chip folder list: recent-first; default cap
 * {@link DRAFT_FOLDER_PREVIEW_LIMIT}; filter / expand shows full match set;
 * the selected folder always stays visible when capped.
 */
export function visibleDraftFolders(opts: {
  folders: FolderMeta[];
  conversations: { folderId?: string | null; updatedAt: string }[];
  query: string;
  expanded: boolean;
  selectedFolderId?: string | null;
}): {
  visible: FolderMeta[];
  matchCount: number;
  canExpand: boolean;
  hiddenCount: number;
} {
  const sorted = sortFoldersByRecentActivity(opts.folders, opts.conversations);
  const matched = filterFoldersByName(sorted, opts.query);
  const matchCount = matched.length;
  const filtering = opts.query.trim().length > 0;

  if (filtering || opts.expanded || matchCount <= DRAFT_FOLDER_PREVIEW_LIMIT) {
    return {
      visible: matched,
      matchCount,
      canExpand: !filtering && matchCount > DRAFT_FOLDER_PREVIEW_LIMIT,
      hiddenCount: 0,
    };
  }

  let visible = matched.slice(0, DRAFT_FOLDER_PREVIEW_LIMIT);
  const selectedId = opts.selectedFolderId;
  if (selectedId && !visible.some((f) => f.id === selectedId)) {
    const selected = matched.find((f) => f.id === selectedId);
    if (selected) visible = [...visible, selected];
  }

  return {
    visible,
    matchCount,
    canExpand: true,
    hiddenCount: matchCount - visible.length,
  };
}
