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

/** Viewport rect for anchoring the「新建文件夹」cascade near a trigger. */
export type CreateFolderAnchorRect = {
  top: number;
  left: number;
  width: number;
  height: number;
};

/** Where a new folder should land — null / absent = 我的文件 top level. */
export type CreateFolderParent = { id: string; name: string };

/** Prefill for {@link useFoldersStore}'s `openImportToCloud` / `openBorrowToCloud`. */
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
  /** A just-created folder whose header should open in inline-rename mode. */
  pendingRenameId: string | null;
  /** Where the current draft will land on first send. */
  draftWorkspaceIntent: DraftWorkspaceIntent;
  /** User-pinned folders shown at the top of workspace pickers. */
  pinnedFolderIds: string[];
  /** Canonical「新建文件夹」cascade (command palette / chip / rail +). */
  createFolderOpen: boolean;
  /** Optional trigger rect; null → host centers the cascade. */
  createFolderAnchor: CreateFolderAnchorRect | null;
  /** Nest the new folder here (rail「在此新建文件夹」); null = top level. */
  createFolderParent: CreateFolderParent | null;
  /** Composer / palette「连接 Git」→ G3 云 clone 对话框。 */
  connectGitOpen: boolean;
  /**
   * Target cloud wsId (`folder:…` / `conv:…`). Null = 先建云文件夹再 clone
   *（入口「连接 Git = 云 clone remote」）。
   */
  connectGitWsId: string | null;
  /** 命令面板 / 文件中枢「导入到云」→ 本机夹快照上传对话框。Composer 三选不走此框。 */
  importToCloudOpen: boolean;
  /**
   * Optional prefill for legacy local migrate：已有 `Folder.localRootId` /
   * 有效根 id，少一次选夹；找不到仍走 picker。
   */
  importToCloudPrefill: ImportToCloudPrefill | null;
  /** 命令面板 / 文件中枢「云上做完再写入」→ 借用云拷贝对话框。Composer 三选不走此框。 */
  borrowToCloudOpen: boolean;
  /** Optional prefill when the caller already picked the local folder. */
  borrowToCloudPrefill: ImportToCloudPrefill | null;

  setPendingRename: (id: string | null) => void;
  setDraftWorkspaceIntent: (intent: DraftWorkspaceIntent) => void;
  resetDraftWorkspaceIntent: () => void;
  openCreateFolder: (
    anchorEl?: Element | null,
    parent?: CreateFolderParent | null,
  ) => void;
  closeCreateFolder: () => void;
  openConnectGit: (wsId?: string | null) => void;
  closeConnectGit: () => void;
  openImportToCloud: (prefill?: ImportToCloudPrefill | null) => void;
  closeImportToCloud: () => void;
  openBorrowToCloud: (prefill?: ImportToCloudPrefill | null) => void;
  closeBorrowToCloud: () => void;
  togglePinFolder: (id: string) => void;
}

function rectFromEl(
  el: Element | null | undefined,
): CreateFolderAnchorRect | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

export const useFoldersStore = create<FoldersUiState>()(
  persist(
    (set) => ({
      pendingRenameId: null,
      draftWorkspaceIntent: defaultDraftWorkspaceIntent(),
      pinnedFolderIds: [],
      createFolderOpen: false,
      createFolderAnchor: null,
      createFolderParent: null,
      connectGitOpen: false,
      connectGitWsId: null,
      importToCloudOpen: false,
      importToCloudPrefill: null,
      borrowToCloudOpen: false,
      borrowToCloudPrefill: null,
      setPendingRename: (id) => set({ pendingRenameId: id }),
      setDraftWorkspaceIntent: (intent) =>
        set({ draftWorkspaceIntent: intent }),
      resetDraftWorkspaceIntent: () =>
        set({ draftWorkspaceIntent: defaultDraftWorkspaceIntent() }),
      openCreateFolder: (anchorEl, parent) => {
        // Capture rect before the trigger menu unmounts / reflows.
        const rect = rectFromEl(anchorEl);
        set({
          createFolderOpen: true,
          createFolderAnchor: rect,
          createFolderParent: parent ?? null,
        });
      },
      closeCreateFolder: () =>
        set({
          createFolderOpen: false,
          createFolderAnchor: null,
          createFolderParent: null,
        }),
      openConnectGit: (wsId) =>
        set({
          connectGitOpen: true,
          connectGitWsId: wsId ?? null,
        }),
      closeConnectGit: () =>
        set({ connectGitOpen: false, connectGitWsId: null }),
      openImportToCloud: (prefill) =>
        set({
          importToCloudOpen: true,
          importToCloudPrefill: prefill ?? null,
        }),
      closeImportToCloud: () =>
        set({ importToCloudOpen: false, importToCloudPrefill: null }),
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
