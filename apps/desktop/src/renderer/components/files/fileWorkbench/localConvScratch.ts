import { conversationIdOf } from "@/components/files/fileWorkbench/storage";
import { folderActivityMs } from "@/lib/draftWorkspaceFolders";
import type { FileSource } from "@/lib/fileSource";
import type { FolderMeta } from "@/services/folders";
import type { WorkspaceInfo } from "@/services/workspaces";

/** Top-level names that are not user-visible desk files (convention / transfer). */
const INTERNAL_SCRATCH_ROOT_NAMES = new Set([
  "AgentCore",
  ".agentcore",
  "attachments",
]);

/** Server / auto-desk names that must not label a local conv: row. */
const REJECTED_LOCAL_CONV_NAMES = new Set(["云文件夹", "未命名对话"]);

const LOCAL_CONV_FALLBACK = "对话文件";

export function isInternalScratchRootName(name: string): boolean {
  const normalized = name.replace(/\\/g, "/").replace(/\/+$/, "");
  const base = normalized.includes("/")
    ? normalized.slice(normalized.lastIndexOf("/") + 1)
    : normalized;
  return INTERNAL_SCRATCH_ROOT_NAMES.has(base);
}

/**
 * True when the scratch root has an entry besides `AgentCore/` / `.agentcore` /
 * `attachments/`. Missing dir or list failure → false (omit the hub row).
 */
export async function scratchHasUserVisibleFiles(
  source: Pick<FileSource, "listDir">,
): Promise<boolean> {
  try {
    const entries = await source.listDir("");
    return entries.some((e) => !isInternalScratchRootName(e.name || e.path));
  } catch {
    return false;
  }
}

/** Sidebar title, else workspace name, else「对话文件」— never 云文件夹 / 未命名对话. */
export function localConvDeskLabel(
  ws: Pick<WorkspaceInfo, "wsId" | "name">,
  conversations: readonly { id: string; title: string }[],
): string {
  const cid = conversationIdOf(ws.wsId);
  const fromConv = cid
    ? conversations.find((c) => c.id === cid)?.title?.trim()
    : undefined;
  if (fromConv) return fromConv;
  const fromWs = ws.name.trim();
  if (fromWs && !REJECTED_LOCAL_CONV_NAMES.has(fromWs)) return fromWs;
  return LOCAL_CONV_FALLBACK;
}

export type LocalRailItem =
  | { kind: "folder"; folder: FolderMeta }
  | { kind: "conv"; ws: WorkspaceInfo };

function convActivityMs(
  wsId: string,
  conversations: readonly { id: string; updatedAt: string }[],
): number {
  const cid = conversationIdOf(wsId);
  if (!cid) return 0;
  const conv = conversations.find((c) => c.id === cid);
  if (!conv) return 0;
  const t = Date.parse(conv.updatedAt);
  return Number.isFinite(t) ? t : 0;
}

function itemName(item: LocalRailItem): string {
  return item.kind === "folder" ? item.folder.name : item.ws.name;
}

/** Mix opened local folders with visible local conv: desks; recent activity first. */
export function mixLocalRailItems(
  folders: FolderMeta[],
  convDesks: WorkspaceInfo[],
  conversations: readonly {
    id: string;
    folderId?: string | null;
    updatedAt: string;
  }[],
): LocalRailItem[] {
  const rows: { item: LocalRailItem; activity: number }[] = [
    ...folders.map((folder) => ({
      item: { kind: "folder" as const, folder },
      activity: folderActivityMs(folder.id, conversations),
    })),
    ...convDesks.map((ws) => ({
      item: { kind: "conv" as const, ws },
      activity: convActivityMs(ws.wsId, conversations),
    })),
  ];
  rows.sort((a, b) => {
    const d = b.activity - a.activity;
    if (d !== 0) return d;
    return itemName(a.item).localeCompare(itemName(b.item), "zh");
  });
  return rows.map((r) => r.item);
}
