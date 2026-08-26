import { addFolderCache } from "@/hooks/useFolders";
import { pickLocalFolderRoot } from "@/lib/bindLocalFolder";
import { createFolder } from "@/services/folders";
import { wsCreateDir, wsUploadFile } from "@/services/workspaces";
import { useFoldersStore } from "@/stores/folders";
import type { FsRoot, WorkspaceOpResult } from "@shared/ipc-contract";
import JSZip from "jszip";

/** Align server ``workspace_upload_max_bytes`` — skip oversized leaves on PUT. */
export const IMPORT_PUT_MAX_BYTES = 50 * 1024 * 1024;

export type ArchivePayload = {
  archive: string;
  file_count: number;
  total_bytes: number;
  truncated: boolean;
};

export type ImportToCloudDeps = {
  pickRoot: () => ReturnType<typeof pickLocalFolderRoot>;
  archiveRoot: (rootId: string) => Promise<WorkspaceOpResult>;
  createCloudFolder: (name: string) => ReturnType<typeof createFolder>;
  uploadFile: typeof wsUploadFile;
  createDir: typeof wsCreateDir;
  removeRoot: (rootId: string) => Promise<void>;
  setDraftIntent: (folderId: string) => void;
  addFolderToCache: (folder: {
    id: string;
    name: string;
    mode: "local" | "cloud";
    localRootId: string | null;
    localSubpath: string | null;
  }) => void;
};

export type ImportToCloudProgress =
  | { phase: "picking" }
  | { phase: "archiving" }
  | { phase: "creating" }
  | { phase: "uploading"; done: number; total: number }
  | { phase: "done" };

export type ImportToCloudResult = {
  folderId: string;
  folderName: string;
  wsId: string;
  uploaded: number;
  skippedOversized: string[];
  archiveTruncated: boolean;
  /** True when archive was truncated and/or any file skipped for PUT size. */
  partial: boolean;
};

/** Human-readable progress for toast / dialog. */
export function formatImportToCloudProgress(
  p: ImportToCloudProgress | null,
): string {
  if (!p) return "";
  switch (p.phase) {
    case "picking":
      return "选择本机文件夹…";
    case "archiving":
      return "正在准备文件…";
    case "creating":
      return "创建文件夹…";
    case "uploading":
      return p.total > 0 ? `上传中 ${p.done}/${p.total}…` : "上传中…";
    case "done":
      return "完成";
  }
}

function throwIfAborted(
  signal: AbortSignal | undefined,
  folder?: { id: string; name: string } | null,
): void {
  if (!signal?.aborted) return;
  throw new ImportToCloudCancelledError(
    folder ? { folderId: folder.id, folderName: folder.name } : undefined,
  );
}

function defaultDeps(): ImportToCloudDeps {
  return {
    pickRoot: () => pickLocalFolderRoot(),
    archiveRoot: async (rootId) => {
      const fsApi = window.fsApi;
      if (!fsApi?.workspaceOp) {
        return {
          ok: false,
          error: {
            kind: "Unavailable",
            detail: "本机目录仅桌面端可用",
          },
        };
      }
      // Large trees: give archive room beyond default IPC races.
      return fsApi.workspaceOp(
        rootId,
        "archive",
        { ignore: true },
        10 * 60_000,
      );
    },
    createCloudFolder: (name) => createFolder({ name, mode: "cloud" }),
    uploadFile: wsUploadFile,
    createDir: wsCreateDir,
    removeRoot: async (rootId) => {
      await window.fsApi?.removeRoot?.(rootId);
    },
    setDraftIntent: (folderId) => {
      useFoldersStore.getState().setDraftWorkspaceIntent({
        kind: "folder",
        folderId,
      });
    },
    addFolderToCache: addFolderCache,
  };
}

export function parseArchivePayload(value: unknown): ArchivePayload | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (typeof v.archive !== "string") return null;
  return {
    archive: v.archive,
    file_count: typeof v.file_count === "number" ? v.file_count : 0,
    total_bytes: typeof v.total_bytes === "number" ? v.total_bytes : 0,
    truncated: v.truncated === true,
  };
}

/**
 * Honest toast after import. Always remind: new folder in 我的文件 + continue
 * there; current session stays on the old local folder (no rebind).
 */
export function formatImportToCloudToast(result: ImportToCloudResult): {
  message: string;
  description?: string;
} {
  const continueHint = "后续请在新文件夹里继续；当前对话用的还是本机原文件夹";
  if (!result.partial) {
    const uploadBit =
      result.uploaded > 0
        ? `已上传 ${result.uploaded} 个文件。`
        : "文件夹已创建（无文件可传）。";
    return {
      message: `已在「我的文件」建好「${result.folderName}」`,
      description: `${uploadBit}${continueHint}`,
    };
  }
  const bits: string[] = [];
  if (result.archiveTruncated) {
    bits.push("内容超过 100MiB 或 2 万个文件，只导入了一部分");
  }
  if (result.skippedOversized.length > 0) {
    bits.push(`跳过 ${result.skippedOversized.length} 个超过 25MiB 的文件`);
  }
  return {
    message: `已在「我的文件」建好「${result.folderName}」（部分导入）`,
    description: `${bits.join("；")}。已上传 ${result.uploaded} 个文件。${continueHint}。`,
  };
}

/** Cancel toast — keep the folder when already created (may be incomplete). */
export function formatImportToCloudCancelledToast(
  err: ImportToCloudCancelledError,
): { message: string; description?: string } {
  if (err.folderId && err.folderName) {
    return {
      message: `已取消导入；文件夹「${err.folderName}」已保留`,
      description:
        "上传未完成，文件夹里的内容可能不全。可以稍后重新导入，或自行删除。",
    };
  }
  return { message: "已取消导入" };
}

/**
 * §五 导入到「我的文件」：本机选夹（临时 root）→ ignore archive → 新建云文件夹 →
 * 逐文件 PUT → draft intent 落到该云桌。禁 mode=local。
 *
 * `signal` 贯穿上传循环；取消后保留已建文件夹（不完整）。`ownsRoot` 为 true
 *（或本函数自行 pick）时 finally `removeRoot`。
 */
export async function runImportToCloud(opts?: {
  folderName?: string;
  /** When set, skip the picker and archive this root (Dialog already picked). */
  root?: FsRoot;
  /**
   * When true with `root`, this run owns cleanup (Dialog handed off). Prefill
   * shared roots stay `false` so we never removeRoot a bound local folder.
   */
  ownsRoot?: boolean;
  signal?: AbortSignal;
  deps?: Partial<ImportToCloudDeps>;
  onProgress?: (p: ImportToCloudProgress) => void;
}): Promise<ImportToCloudResult> {
  const deps: ImportToCloudDeps = { ...defaultDeps(), ...opts?.deps };
  const onProgress = opts?.onProgress;
  const signal = opts?.signal;

  let root = opts?.root;
  let ownRoot = opts?.ownsRoot === true;
  if (!root) {
    onProgress?.({ phase: "picking" });
    throwIfAborted(signal);
    const picked = await deps.pickRoot();
    throwIfAborted(signal);
    if (!picked.ok) {
      if (picked.reason === "cancelled") {
        throw new ImportToCloudCancelledError();
      }
      throw new Error(picked.message);
    }
    root = picked.root;
    ownRoot = true;
  }

  let createdFolder: { id: string; name: string } | null = null;

  try {
    throwIfAborted(signal);
    onProgress?.({ phase: "archiving" });
    const archRes = await deps.archiveRoot(root.id);
    throwIfAborted(signal);
    if (!archRes.ok) {
      throw new Error(archRes.error.detail || "打包失败");
    }
    const payload = parseArchivePayload(archRes.value);
    if (!payload) {
      throw new Error("打包结果无效");
    }

    const folderName =
      opts?.folderName?.trim() || root.name.trim() || "导入的文件夹";

    throwIfAborted(signal);
    onProgress?.({ phase: "creating" });
    const { folder } = await deps.createCloudFolder(folderName);
    createdFolder = { id: folder.id, name: folder.name };
    deps.addFolderToCache(folder);
    deps.setDraftIntent(folder.id);
    const wsId = `folder:${folder.id}`;

    throwIfAborted(signal, createdFolder);
    const zip = await JSZip.loadAsync(payload.archive, { base64: true });
    throwIfAborted(signal, createdFolder);
    const entries = Object.values(zip.files).filter((e) => !e.dir);
    const skippedOversized: string[] = [];
    let uploaded = 0;
    const total = entries.length;

    for (let i = 0; i < entries.length; i++) {
      throwIfAborted(signal, createdFolder);
      const entry = entries[i];
      if (!entry) continue;
      onProgress?.({ phase: "uploading", done: i, total });
      const path = entry.name.replace(/^\/+/, "").replace(/\\/g, "/");
      if (!path || path.includes("..")) continue;
      const buf = await entry.async("uint8array");
      throwIfAborted(signal, createdFolder);
      if (buf.byteLength > IMPORT_PUT_MAX_BYTES) {
        skippedOversized.push(path);
        continue;
      }
      const slash = path.lastIndexOf("/");
      if (slash > 0) {
        // Best-effort parent dir (PUT also creates parents; empty dirs rare).
        try {
          await deps.createDir(wsId, path.slice(0, slash));
        } catch {
          // ignore — upload may still succeed
        }
      }
      throwIfAborted(signal, createdFolder);
      await deps.uploadFile(wsId, path, new Blob([buf as BlobPart]));
      uploaded += 1;
    }
    throwIfAborted(signal, createdFolder);
    onProgress?.({ phase: "uploading", done: total, total });

    const partial = payload.truncated || skippedOversized.length > 0;
    onProgress?.({ phase: "done" });
    return {
      folderId: folder.id,
      folderName: folder.name,
      wsId,
      uploaded,
      skippedOversized,
      archiveTruncated: payload.truncated,
      partial,
    };
  } finally {
    if (ownRoot) {
      try {
        await deps.removeRoot(root.id);
      } catch {
        // temp root leak is non-fatal
      }
    }
  }
}

export class ImportToCloudCancelledError extends Error {
  readonly folderId?: string;
  readonly folderName?: string;

  constructor(opts?: { folderId?: string; folderName?: string }) {
    super("cancelled");
    this.name = "ImportToCloudCancelledError";
    this.folderId = opts?.folderId;
    this.folderName = opts?.folderName;
  }
}
