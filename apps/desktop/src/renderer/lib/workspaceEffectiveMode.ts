import { LOCAL_TRADITIONAL_LABEL } from "@/lib/conversationWorkspaceMode";
import type { WorkspaceBinding } from "@/services/workspaceBinding";
import { isBoundRootMissing } from "@/services/workspaceBinding";
import type { FsRoot } from "@shared/ipc-contract";

/**
 * Effective workspace location for UI chips / mode bars.
 *
 * Turn routing uses `local_root_id` **or** `local_container_root_id` (sidecar /
 * cloud-turn binding). Project conversations inherit the folder bind
 * (`scope=folder`); bare chats use conversation bind or default container.
 */
export interface EffectiveWorkspace {
  /** True when turns route to a local root (bound folder or default container). */
  isLocal: boolean;
  /** Root id used for display / missing-root checks (bound root preferred). */
  rootId: string | null;
  /** Human folder name when resolvable from the desktop root list. */
  rootName: string | null;
  /** Explicit bind whose root is gone on this device (§八). */
  rootMissing: boolean;
  /** True when locality comes from default container, not an explicit bind. */
  viaContainer: boolean;
  /** Project name when the conversation inherits a folder workspace. */
  folderName: string | null;
  /** Binding lives on the project (vs bare conversation scratch). */
  viaFolder: boolean;
}

export function resolveEffectiveWorkspace(opts: {
  binding: WorkspaceBinding | null;
  localContainerRootId: string | null | undefined;
  roots: readonly FsRoot[];
  folderName?: string | null;
}): EffectiveWorkspace {
  const { binding, localContainerRootId, roots, folderName = null } = opts;
  const viaFolder = binding?.scope === "folder";
  const boundRootId =
    binding?.mode === "local" && binding.rootId ? binding.rootId : null;

  if (boundRootId) {
    const rootName = roots.find((r) => r.id === boundRootId)?.name ?? null;
    return {
      isLocal: true,
      rootId: boundRootId,
      rootName,
      rootMissing: isBoundRootMissing(binding, roots),
      viaContainer: binding?.source === "container",
      folderName: viaFolder ? folderName : null,
      viaFolder,
    };
  }

  if (localContainerRootId) {
    const rootName =
      roots.find((r) => r.id === localContainerRootId)?.name ?? null;
    return {
      isLocal: true,
      rootId: localContainerRootId,
      rootName,
      rootMissing: !roots.some((r) => r.id === localContainerRootId),
      viaContainer: true,
      folderName: null,
      viaFolder: false,
    };
  }

  return {
    isLocal: false,
    rootId: null,
    rootName: null,
    rootMissing: false,
    viaContainer: false,
    folderName: viaFolder ? folderName : null,
    viaFolder,
  };
}

/**
 * Chip / mode-bar label（可见短标；有归属的会话只留文件夹名，通道靠图标 + title）:
 * - folder（local / cloud）: 「文件夹名」
 * - bare local: 「本地对话」
 * - bare cloud: 「云端对话」
 */
export function formatWorkspaceChipLabel(ws: EffectiveWorkspace): string {
  if (ws.viaFolder && ws.folderName) return ws.folderName;
  if (ws.isLocal) return "本地对话";
  return "云端对话";
}

/**
 * Bound workspace chip `title` / `aria-label`：可见文案无通道后缀时，在此说清工作区绑定
 * （文件夹绑定，≠ 执行路径）。执行路径不在大众 Composer 产品面展示。
 */
export function formatWorkspaceChipTitle(ws: EffectiveWorkspace): string {
  if (ws.viaFolder) {
    return ws.isLocal
      ? `${LOCAL_TRADITIONAL_LABEL}（本机文件夹权威，≠离线）`
      : "云端对话";
  }
  return ws.isLocal
    ? "本地对话（文件落本机默认目录，未归入文件夹）"
    : "云端对话";
}
