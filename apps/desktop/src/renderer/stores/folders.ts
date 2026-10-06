import { hasLocalFiles } from "@/lib/capabilities";
import { getComposerChannelPreference } from "@/lib/composerChannelPreference";
import { createZustandUiStorage } from "@/lib/uiStorage";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/** Filter key for the synthetic "ungrouped" section (not a real folder). */
export const UNGROUPED_KEY = "__ungrouped__";

const uiPersistStorage = createJSONStorage(() => createZustandUiStorage());

/**
 * Draft-time「在哪工作」intent — single discriminant union.
 * Desktop default = quick local scratch（`~/Documents/AgentCore/conversations/<id>`）。
 * Web / 手机无本机盘 → quick cloud。
 */
export type DraftWorkspaceIntent =
  | { kind: "quick_local" }
  | { kind: "quick_cloud" }
  | { kind: "folder"; folderId: string };

export function defaultDraftWorkspaceIntent(): DraftWorkspaceIntent {
  if (!hasLocalFiles()) return { kind: "quick_cloud" };
  return getComposerChannelPreference() === "cloud"
    ? { kind: "quick_cloud" }
    : { kind: "quick_local" };
}

/** Nest the new untitled cloud folder here; omit / null = 我的文件 top level. */
export type UntitledFolderParentId = string | null;

/** Prefill for {@link useFoldersStore}'s `openBorrowToCloud`. */
export type ImportToCloudPrefill = {
  /** Existing desktop `FsRoot.id` (e.g. Folder.localRootId). */
  rootId?: string | null;
  /** Suggested name for the folder created in 我的文件. */
  folderName?: string | null;
  /**
   * Caller already authorized this root. True → dialog cancel may removeRoot;
   * false → shared binding (legacy migrate / existing local folder), leave it.
   */
  ownsRoot?: boolean;
};

/**
 * Pure-UI folder state. The folder *list* is server data owned by React Query
 * (see `hooks/useFolders`); only these ephemeral, view-only flags — which a
 * cache doesn't model — live here, coordinating one-shot UI handoffs between the
 * folder-CRUD action and the component that should react to it.
 */
interface FoldersUiState {
  /**
   * A just-created cloud folder the files rail should expand + flash.
   * Named creates (Composer) reveal only; untitled creates also rename in place.
   */
  pendingRevealFolderId: string | null;
  /** Enter inline rename on this folder row once it is mounted. */
  pendingRenameFolderId: string | null;
  /**
   * Files rail / command palette asked to POST「未命名文件夹」.
   * FileWorkbench is the only consumer (needs the list + rename row).
   */
  pendingUntitledCreate: { parentId: UntitledFolderParentId } | null;
  untitledCreateBusy: boolean;
  /** Where the current draft will land on first send. */
  draftWorkspaceIntent: DraftWorkspaceIntent;
  /** User-pinned folders shown at the top of workspace pickers. */
  pinnedFolderIds: string[];
  /** Composer / palette「连接 Git」→ G3 云 clone 对话框。 */
  connectGitOpen: boolean;
  /**
   * Target cloud wsId (`folder:…` / `conv:…`). Null = 先建云文件夹再 clone
   *（入口「连接 Git = 云 clone remote」）。
   */
  connectGitWsId: string | null;
  /** 命令面板「云上做完再写入」→ 借用云拷贝对话框。Composer 点本机文件夹不再进此框。 */
  borrowToCloudOpen: boolean;
  /** Optional prefill when the caller already picked the local folder. */
  borrowToCloudPrefill: ImportToCloudPrefill | null;

  revealCreatedFolder: (id: string, opts?: { rename?: boolean }) => void;
  clearPendingReveal: () => void;
  clearPendingRename: () => void;
  requestUntitledCloudFolder: (parentId?: UntitledFolderParentId) => void;
  clearUntitledCreateRequest: () => void;
  finishUntitledCreate: () => void;
  setDraftWorkspaceIntent: (intent: DraftWorkspaceIntent) => void;
  resetDraftWorkspaceIntent: () => void;
  openConnectGit: (wsId?: string | null) => void;
  closeConnectGit: () => void;
  openBorrowToCloud: (prefill?: ImportToCloudPrefill | null) => void;
  closeBorrowToCloud: () => void;
  togglePinFolder: (id: string) => void;
}

export const useFoldersStore = create<FoldersUiState>()(
  persist(
    (set) => ({
      pendingRevealFolderId: null,
      pendingRenameFolderId: null,
      pendingUntitledCreate: null,
      untitledCreateBusy: false,
      draftWorkspaceIntent: defaultDraftWorkspaceIntent(),
      pinnedFolderIds: [],
      connectGitOpen: false,
      connectGitWsId: null,
      borrowToCloudOpen: false,
      borrowToCloudPrefill: null,
      revealCreatedFolder: (id, opts) =>
        set({
          pendingRevealFolderId: id,
          pendingRenameFolderId: opts?.rename ? id : null,
        }),
      clearPendingReveal: () => set({ pendingRevealFolderId: null }),
      clearPendingRename: () => set({ pendingRenameFolderId: null }),
      requestUntitledCloudFolder: (parentId) =>
        set((s) =>
          s.pendingUntitledCreate || s.untitledCreateBusy
            ? s
            : {
                pendingUntitledCreate: { parentId: parentId ?? null },
                untitledCreateBusy: true,
              },
        ),
      clearUntitledCreateRequest: () => set({ pendingUntitledCreate: null }),
      finishUntitledCreate: () => set({ untitledCreateBusy: false }),
      setDraftWorkspaceIntent: (intent) =>
        set({ draftWorkspaceIntent: intent }),
      resetDraftWorkspaceIntent: () =>
        set({ draftWorkspaceIntent: defaultDraftWorkspaceIntent() }),
      openConnectGit: (wsId) =>
        set({
          connectGitOpen: true,
          connectGitWsId: wsId ?? null,
        }),
      closeConnectGit: () =>
        set({ connectGitOpen: false, connectGitWsId: null }),
      openBorrowToCloud: (prefill) =>
        set({
          borrowToCloudOpen: true,
          borrowToCloudPrefill: prefill ?? null,
        }),
      closeBorrowToCloud: () =>
        set({ borrowToCloudOpen: false, borrowToCloudPrefill: null }),
      togglePinFolder: (id) =>
        set((s) => ({
          pinnedFolderIds: s.pinnedFolderIds.includes(id)
            ? s.pinnedFolderIds.filter((x) => x !== id)
            : [...s.pinnedFolderIds, id],
        })),
    }),
    {
      name: "folders-ui",
      storage: uiPersistStorage,
      partialize: (s) => ({ pinnedFolderIds: s.pinnedFolderIds }),
    },
  ),
);
