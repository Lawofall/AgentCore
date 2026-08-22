import { addFolderCache, getFolders } from "@/hooks/useFolders";
import {
  type LocalPickerFailureKind,
  localPickerFailureCopy,
  notifyLocalPickerFailure,
  pickLocalFolderRoot,
} from "@/lib/bindLocalFolder";
import { hasLocalFiles } from "@/lib/capabilities";
import { startNewConversation } from "@/lib/newConversation";
import { notifyError } from "@/lib/toast";
import { resolveSidecarAccountAuth } from "@/services/accountToken";
import {
  type FolderMeta,
  createFolder,
  findLocalFolderByBinding,
} from "@/services/folders";
import { useAuthStore } from "@/stores/auth";
import type { FsRoot } from "@shared/ipc-contract";
import type { NavigateFunction } from "react-router-dom";

/** Answer text so the CEO/worker LLM sees which folder was opened (new session). */
export function formatOpenLocalFolderAnswer(
  optionLabel: string,
  folderName: string,
): string {
  return `${optionLabel}（${folderName} · 已打开为本机文件夹，新会话）`;
}

export type PickAndOpenLocalFolderResult =
  | { ok: true; root: FsRoot; folder: FolderMeta; created: boolean }
  | { ok: false; reason: "cancelled" }
  | {
      ok: false;
      reason: LocalPickerFailureKind;
      message: string;
    };

/**
 * Create/reuse a local Folder for an already-authorized root, then start a
 * **new** conversation under it. Composer「从本机加入」picks first, then asks
 * how to use the path — this is the「直接改这个文件夹」leg.
 *
 * Does **not** rewrite the current session's ``folder_id`` (出生定终身).
 * Distinct from {@link pickAndBindLocalFolder} (bare-chat scratch execution bind).
 */
export async function openLocalFolderFromRoot(
  root: FsRoot,
  navigate: NavigateFunction,
  opts?: { notifyOnFailure?: boolean },
): Promise<PickAndOpenLocalFolderResult> {
  const notifyOnFailure = opts?.notifyOnFailure !== false;
  try {
    const existing = findLocalFolderByBinding(getFolders(), root.id, null);
    let folder: FolderMeta;
    let created: boolean;
    if (existing) {
      folder = existing;
      created = false;
    } else {
      const result = await createFolder({
        name: root.name,
        mode: "local",
        localRootId: root.id,
        localSubpath: null,
      });
      folder = result.folder;
      created = result.created;
      addFolderCache(folder);
    }

    startNewConversation(navigate, folder.id);
    // Silent Cursor-style index + MCP + rules/memory warm: ensure sidecar (fire-and-forget).
    if (window.sidecarApi?.warmCodeIndex) {
      void window.sidecarApi
        .warmCodeIndex({ rootId: root.id, subpath: "" })
        .catch(() => {
          /* best-effort; no toast */
        });
    }
    if (window.sidecarApi?.warmMcpDiscover) {
      void window.sidecarApi
        .warmMcpDiscover({
          rootId: root.id,
          subpath: "",
          userId: useAuthStore.getState().user?.id,
        })
        .catch(() => {
          /* best-effort; no toast */
        });
    }
    if (window.sidecarApi?.warmAccountRulesMemory) {
      void (async () => {
        const accountAuth = (await resolveSidecarAccountAuth()) ?? undefined;
        if (!accountAuth) return;
        await window.sidecarApi?.warmAccountRulesMemory({
          rootId: root.id,
          subpath: "",
          folderId: folder.id,
          accountAuth,
          userId: useAuthStore.getState().user?.id,
        });
      })().catch(() => {
        /* best-effort; no toast */
      });
    }
    return { ok: true, root, folder, created };
  } catch (e) {
    const message =
      e instanceof Error ? e.message : "打开本机文件夹失败，请重试";
    if (notifyOnFailure) {
      notifyError(e, "打开本机文件夹失败");
    }
    return { ok: false, reason: "error", message };
  }
}

/**
 * OS folder picker → {@link openLocalFolderFromRoot}.「打开本机文件夹」in the
 * command palette / Ask / file rail is one of the two ways to get a container
 * at all (双模式工作区 §5.4).
 *
 * Failure kinds are fixed (dialog_failed / unauthorized / …);
 * callers should show the structured card — never loop 「已触发请选择」.
 * No language-specific root marker (e.g. package.json) — any folder qualifies.
 */
export async function pickAndOpenLocalFolder(
  navigate: NavigateFunction,
  opts?: { notifyOnFailure?: boolean },
): Promise<PickAndOpenLocalFolderResult> {
  const notifyOnFailure = opts?.notifyOnFailure !== false;
  if (!hasLocalFiles() || !window.fsApi) {
    const message = localPickerFailureCopy("unavailable").detail;
    if (notifyOnFailure) notifyLocalPickerFailure("unavailable", message);
    return { ok: false, reason: "unavailable", message };
  }
  const picked = await pickLocalFolderRoot();
  if (!picked.ok) {
    if (picked.reason === "cancelled") {
      return { ok: false, reason: "cancelled" };
    }
    if (notifyOnFailure) {
      notifyLocalPickerFailure(picked.reason, picked.message);
    }
    return {
      ok: false,
      reason: picked.reason,
      message: picked.message,
    };
  }
  return openLocalFolderFromRoot(picked.root, navigate, opts);
}
