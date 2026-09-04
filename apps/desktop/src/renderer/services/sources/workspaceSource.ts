import { getConversations } from "@/hooks/useConversations";
import { hasInAppPreview } from "@/lib/capabilities";
import {
  type FileIndexListing,
  type FileNode,
  type FilePreviewResult,
  type FileSource,
  baseName,
} from "@/lib/fileSource";
import { formatBytes } from "@/lib/format";
import { openWorkspaceHtmlInBrowser } from "@/lib/openWorkspaceHtmlInBrowser";
import { notifyInfo } from "@/lib/toast";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";
import {
  copyWorkspaceFile,
  createWorkspaceDir,
  deleteWorkspaceFile,
  downloadWorkspaceArchive,
  downloadWorkspaceFile,
  exportWorkspaceMdToDocx,
  fetchWorkspaceFileBlob,
  listWorkspaceFiles,
  moveWorkspaceFile,
  openWorkspaceInBrowser,
  readWorkspaceFile,
  readWorkspaceFileForEdit,
  uploadWorkspaceFile,
  writeWorkspaceFileText,
} from "@/services/workspace";
import type {
  WorkspaceEditDoc,
  WorkspaceFile,
  WorkspaceListing,
  FilePreview as WorkspacePreview,
  WorkspaceWriteOutcome,
} from "@/services/workspaceHttp";
import type { WorkspaceInfo } from "@/services/workspaces";
import {
  openCloudWorkspaceInBrowser,
  wsCopyFile,
  wsCreateDir,
  wsDeleteFile,
  wsDownloadArchive,
  wsDownloadFile,
  wsExportMdToDocx,
  wsFetchFileBlob,
  wsListFileIndex,
  wsListFiles,
  wsMoveFile,
  wsReadFile,
  wsReadFileForEdit,
  wsUploadFile,
  wsWriteFileText,
} from "@/services/workspaces";
import { OPEN_TEMP_FILE_MAX_BYTES } from "@shared/ipc-contract";
import { isSafeOpenExt } from "@shared/openable-ext";
import { createLocalRootSource } from "./localRootSource";

/** Map the cloud preview wire shape into the unified {@link FilePreviewResult}. */
function adaptPreview(p: WorkspacePreview): FilePreviewResult {
  if (p.kind === "text") {
    return { kind: "text", text: p.text, truncated: p.truncated };
  }
  if (p.kind === "image") {
    return {
      kind: "image",
      dataUrl: p.dataUrl,
      mime: p.mime,
      size: p.size,
    };
  }
  if (p.kind === "pdf") {
    return {
      kind: "pdf",
      dataUrl: p.dataUrl,
      mime: p.mime,
      size: p.size,
    };
  }
  if (p.kind === "too-large") return { kind: "too-large" };
  return {
    kind: "binary",
    mime: p.mime,
    size: p.size,
    reason: p.reason,
  };
}

/** Cloud desks that snapshot / preview — folder projects and conversation scratch. */
function isCloudDeskWsId(wsId: string): boolean {
  return wsId.startsWith("folder:") || wsId.startsWith("conv:");
}

/**
 * Hang HTML full-effect exits on a cloud {@link FileSource}.
 *
 * - `openInAppPreview` 跟落地 desk：传当前源的 `folder:` / `conv:` wsId（缺省
 *   `conv:{conversationId}`）；hub `folder:` 在有能力位时同样挂上。
 * - `openInBrowser` via conversation snapshot, or ws-id snapshot for hub
 *   `folder:` / `conv:`。
 */
function withCloudHtmlEntries(
  source: FileSource,
  opts: { conversationId?: string; wsId?: string },
): FileSource {
  const withExtras: FileSource = { ...source };
  const conversationId = opts.conversationId;
  const wsId = opts.wsId;

  if (conversationId) {
    if (window.fsApi?.previewArchive) {
      withExtras.openInBrowser = (path) =>
        openWorkspaceInBrowser(conversationId, path);
    }
  } else if (wsId?.startsWith("folder:") && window.fsApi?.previewArchive) {
    withExtras.openInBrowser = (path) =>
      openCloudWorkspaceInBrowser(wsId, path);
  }

  // 完整预览跟桌：落地 wsId = 显式 desk，否则会话 `conv:{cid}`；仅 folder: / conv:。
  const landingWsId =
    wsId ?? (conversationId ? `conv:${conversationId}` : undefined);
  if (hasInAppPreview() && landingWsId && isCloudDeskWsId(landingWsId)) {
    // hub `folder:` 无会话时用 folder id 作页/分区作用域；desk 仍走 workspaceId。
    const cid =
      conversationId ??
      (landingWsId.startsWith("conv:")
        ? landingWsId.slice("conv:".length)
        : landingWsId.startsWith("folder:")
          ? landingWsId.slice("folder:".length)
          : undefined);
    if (cid) {
      withExtras.openInAppPreview = (path) =>
        openWorkspaceHtmlInBrowser(cid, path, landingWsId);
    }
  }
  return withExtras;
}

/** The cloud workspace caps — shared by the conversation- and ws-id-keyed sources. */
const CLOUD_CAPS = {
  watch: false,
  transfer: true,
  edit: true,
  snapshots: true,
} as const;

/** Viewer / readonly collaboration desk: browse + download, no mutate / in-panel edit. */
const CLOUD_READONLY_CAPS = {
  watch: false,
  transfer: true,
  edit: false,
  snapshots: false,
} as const;

/**
 * The addressing-agnostic REST surface a cloud {@link FileSource} needs. The two
 * cloud sources differ only in *how they address* their workspace (conversation id
 * vs workspace id), so each binds its own client and shares the source body in
 * {@link makeCloudSource} — the file hub and chat panel can't drift on cloud
 * behaviour. `listFileIndex` is optional: only the ws-id client exposes the @ index.
 */
interface CloudFileClient {
  listFiles(opts: {
    recursive?: boolean;
    dir?: string;
  }): Promise<WorkspaceListing>;
  read(path: string): Promise<WorkspacePreview>;
  readForEdit(path: string): Promise<WorkspaceEditDoc>;
  writeText(
    path: string,
    input: { content: string; eol: "lf" | "crlf"; baselineMtimeMs: number },
  ): Promise<WorkspaceWriteOutcome>;
  upload(path: string, body: Blob): Promise<void>;
  createDir(path: string): Promise<void>;
  move(src: string, dst: string): Promise<void>;
  copy(src: string, dst: string): Promise<void>;
  delete(path: string): Promise<void>;
  download(
    path: string,
    filename: string,
    opts?: { isDir?: boolean },
  ): Promise<void>;
  /** Raw bytes (no save dialog) — backs 「用本机默认应用打开」's temp copy. */
  fetchBytes(path: string): Promise<Blob>;
  exportMdToDocx(path: string): Promise<{ path: string; warnings: string[] }>;
  listFileIndex?(): Promise<FileIndexListing>;
}

/**
 * 云端文件「用本机默认应用打开」：取字节 → 主进程落**只读**临时副本 → 系统默认程序。
 *
 * 与本地源同名方法效果不同——本地开的是磁盘上的真实文件，云端开的是本机副本，外部改动
 * 不回写云端。同一个菜单项两种语义，故成功后必须提示，否则用户会以为在外部改完就生效了。
 * 超限（{@link OPEN_TEMP_FILE_MAX_BYTES}）在取到字节后就地拦下并指向「下载」，省掉一次
 * 注定被主进程拒掉的大块 IPC 拷贝。
 */
async function openCloudFileWithOsDefaultApp(
  client: CloudFileClient,
  path: string,
): Promise<void> {
  const openTempFile = window.fsApi?.openTempFile;
  if (!openTempFile) throw new Error("此环境不支持用本机应用打开");
  const blob = await client.fetchBytes(path);
  if (blob.size > OPEN_TEMP_FILE_MAX_BYTES) {
    throw new Error(
      `文件超过 ${formatBytes(OPEN_TEMP_FILE_MAX_BYTES)}，请改用「下载」后再打开`,
    );
  }
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const result = await openTempFile(baseName(path), bytes);
  if (!result.ok) throw new Error(result.message);
  notifyInfo("已用本机默认应用打开", {
    description: "打开的是只读副本，在外部改动不会同步回云端。",
  });
}

/**
 * Path-aware `AgentCore/{index,trash,baselines,versions}` — mirrors main
 * `isInternalZoneRelPath`. Inlined on purpose (renderer must not import the
 * main-process module); the zone list is held to the server's by
 * `check_workspace_ignore_parity.py`, so add new zones here too.
 */
function isInternalZonePath(path: string): boolean {
  const p = path.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!p || p === ".") return false;
  for (const zone of ["index", "trash", "baselines", "versions"] as const) {
    const prefix = `AgentCore/${zone}`;
    if (p === prefix || p.startsWith(`${prefix}/`)) return true;
  }
  return false;
}

function toFileNodes(files: WorkspaceFile[]): FileNode[] {
  return files
    .filter((f) => !isInternalZonePath(f.path))
    .map((f) => ({
      path: f.path,
      name: baseName(f.path),
      isDir: f.isDir,
      sizeBytes: f.sizeBytes,
      mtimeMs: f.mtimeMs,
    }));
}

/**
 * Build a cloud {@link FileSource} over a {@link CloudFileClient}. Shared by both
 * cloud factories so the hub (ws id) and chat panel (conversation id) stay
 * byte-for-byte identical on everything but addressing.
 *
 * Listing strategy (fc35aece): do **not** eager-`listTree` via recursive REST.
 * Every level — root and each expanded subdirectory — asks the server for that one
 * directory. Subdirs used to filter a *recursive* call locally, which shared the
 * root's entry budget: past ~100 files the tail of the alphabet and everything deep
 * simply did not exist in the panel. Omit `listTree` so {@link FileTree} stays lazy.
 * `listDirBounded` carries the server's `truncated` bit up so a level that really
 * did hit the ceiling says so instead of looking complete.
 */
function makeCloudSource(
  key: string,
  label: string,
  client: CloudFileClient,
  caps: typeof CLOUD_CAPS | typeof CLOUD_READONLY_CAPS = CLOUD_CAPS,
): FileSource {
  const listDirBounded = async (
    dir: string,
  ): Promise<{ entries: FileNode[]; truncated: boolean }> => {
    const res = await client.listFiles({ recursive: false, dir: dir || "." });
    return { entries: toFileNodes(res.files), truncated: res.truncated };
  };

  const fileIndex = client.listFileIndex;
  return {
    id: `workspace:${key}`,
    label,
    caps,
    listDir: async (dir) => (await listDirBounded(dir)).entries,
    listDirBounded,
    // Feeds the @ index (文件中枢统一 F4) — flat, files-only, server-pruned/capped.
    ...(fileIndex ? { listFileIndex: fileIndex } : {}),
    read: (path) => client.read(path).then(adaptPreview),
    readForEdit: async (path) => {
      const d = await client.readForEdit(path);
      return {
        text: d.text,
        version: { mtimeMs: d.mtimeMs },
        encoding: "utf-8",
        eol: d.eol,
      };
    },
    writeText: async (path, input) => {
      const r = await client.writeText(path, {
        content: input.content,
        eol: input.eol,
        baselineMtimeMs: input.baseline?.mtimeMs ?? 0,
      });
      return r.ok
        ? { ok: true, version: { mtimeMs: r.mtimeMs } }
        : { ok: false, reason: "conflict", version: { mtimeMs: r.mtimeMs } };
    },
    createFile: (path) => client.upload(path, new Blob([])),
    mkdir: (path) => client.createDir(path),
    move: (src, dst) => client.move(src, dst),
    delete: (path) => client.delete(path),
    writeBytes: (path, body) => client.upload(path, body),
    download: (path, filename, opts) => client.download(path, filename, opts),
    // 桌面专属「用本机默认应用打开」：条件挂载同 previewArchive —— web 运行时不实现
    // `openTempFile`，入口便整个不出现。谓词收白名单：云端字节是 AI 产出的，名单外类型
    // 连入口都不给（主进程另有硬拒的强制面），与本地源「名单外仍可开 + 确认」刻意不同。
    ...(window.fsApi?.openTempFile
      ? {
          openWithOsDefaultApp: (path: string) =>
            openCloudFileWithOsDefaultApp(client, path),
          canOpenWithOsDefaultApp: (path: string) => isSafeOpenExt(path),
        }
      : {}),
    // 写能力跟 caps.edit：无 edit 时不挂 copy / export（菜单与快捷键都靠「方法是否存在」+ canMutate）
    ...(caps.edit
      ? {
          copy: (src: string, dst: string) => client.copy(src, dst),
          exportMdToDocx: (path: string) => client.exportMdToDocx(path),
        }
      : {}),
  };
}

/**
 * A {@link FileSource} over a conversation's server workspace (cloud mode, REST),
 * keyed by conversationId — the chat panel's source (per-conversation alias). The
 * hub addresses the same spaces by workspace id via {@link createCloudWorkspaceSource}.
 */
export function createWorkspaceSource(
  conversationId: string,
  label = "工作区",
): FileSource {
  const source = makeCloudSource(conversationId, label, {
    listFiles: (opts) => listWorkspaceFiles(conversationId, opts),
    read: (path) => readWorkspaceFile(conversationId, path),
    readForEdit: (path) => readWorkspaceFileForEdit(conversationId, path),
    writeText: (path, input) =>
      writeWorkspaceFileText(conversationId, path, input),
    upload: (path, body) => uploadWorkspaceFile(conversationId, path, body),
    createDir: (path) => createWorkspaceDir(conversationId, path),
    move: (src, dst) => moveWorkspaceFile(conversationId, src, dst),
    copy: (src, dst) => copyWorkspaceFile(conversationId, src, dst),
    delete: (path) => deleteWorkspaceFile(conversationId, path),
    download: (path, filename, opts) =>
      opts?.isDir
        ? downloadWorkspaceArchive(conversationId, path, filename)
        : downloadWorkspaceFile(conversationId, path, filename),
    fetchBytes: (path) => fetchWorkspaceFileBlob(conversationId, path),
    exportMdToDocx: (path) => exportWorkspaceMdToDocx(conversationId, path),
  });
  // 桌面专属 HTML 完整效果出口（web stub 不提供 → 面板源码 + 下载兜底）。
  return withCloudHtmlEntries(source, {
    conversationId,
    wsId: `conv:${conversationId}`,
  });
}

/**
 * A {@link FileSource} over a **cloud** workspace addressed by its workspace id
 * (`/v1/workspaces/{wsId}`, 文件中枢统一 Step 2) — the hub's source for cloud
 * projects. Identical shape to {@link createWorkspaceSource}; only the addressing
 * (ws id vs conversation id) differs, plus the @ index this exposes. Local
 * workspaces never use this — the hub picks `LocalRootSource` (IPC) for them (§五).
 */
export function createCloudWorkspaceSource(
  wsId: string,
  label = "工作区",
  opts?: { readonly?: boolean },
): FileSource {
  const readonly = !!opts?.readonly;
  const source = makeCloudSource(
    wsId,
    label,
    {
      listFiles: (q) => wsListFiles(wsId, q),
      read: (path) => wsReadFile(wsId, path),
      readForEdit: (path) => wsReadFileForEdit(wsId, path),
      writeText: (path, input) => wsWriteFileText(wsId, path, input),
      upload: (path, body) => wsUploadFile(wsId, path, body),
      createDir: (path) => wsCreateDir(wsId, path),
      move: (src, dst) => wsMoveFile(wsId, src, dst),
      copy: (src, dst) => wsCopyFile(wsId, src, dst),
      delete: (path) => wsDeleteFile(wsId, path),
      download: (path, filename, opts) =>
        opts?.isDir
          ? wsDownloadArchive(wsId, path, filename)
          : wsDownloadFile(wsId, path, filename),
      fetchBytes: (path) => wsFetchFileBlob(wsId, path),
      exportMdToDocx: (path) => wsExportMdToDocx(wsId, path),
      listFileIndex: () => wsListFileIndex(wsId),
    },
    readonly ? CLOUD_READONLY_CAPS : CLOUD_CAPS,
  );
  // Hub cloud sources: `conv:` / `folder:` → 完整预览跟桌。
  if (wsId.startsWith("conv:")) {
    return withCloudHtmlEntries(source, {
      conversationId: wsId.slice("conv:".length),
      wsId,
    });
  }
  if (wsId.startsWith("folder:")) {
    return withCloudHtmlEntries(source, { wsId });
  }
  return source;
}

/**
 * Resolve a {@link WorkspaceInfo} to its {@link FileSource} — the single home for
 * "which file backend for this workspace": cloud → REST by ws id
 * ({@link createCloudWorkspaceSource}), local → desktop IPC over the bound root
 * ({@link createLocalRootSource}). Shared by the 文件 hub ({@link FileWorkbench}) and
 * the conversation side panel so the two can never drift on cloud/local selection
 * (the drift that let an agent's local file write go unseen by the cloud-only panel).
 *
 * Local resolves only on desktop (needs `window.fsApi` + a bound root); a non-empty
 * `ws.subpath` (工作区对称化 D1a) scopes the local source to that subtree under the
 * shared container root. Returns null when a local workspace can't be served here
 * (no fsApi / no root) so callers render the "在桌面端查看" degradation instead.
 */
export function resolveWorkspaceSource(
  ws: WorkspaceInfo,
  fsAvailable: boolean,
): FileSource | null {
  if (ws.location === "local") {
    if (!fsAvailable || !ws.rootId) return null;
    return createLocalRootSource(ws.rootId, ws.name, ws.subpath);
  }
  return createCloudWorkspaceSource(ws.wsId, ws.name);
}

/**
 * When the workspace list has no `conv:<id>` row yet, fall back to the same
 * local target resolution sidecar uses (`localContainerRootId` + workspace-cache
 * subpath). Returns null when no on-machine local binding exists — callers then
 * use the conversation-keyed cloud REST source.
 */
export async function resolveConversationLocalFileSource(
  conversationId: string,
): Promise<FileSource | null> {
  const target = await resolveConversationLocalTarget(conversationId);
  if (!target) return null;

  const roots = await window.fsApi.listRoots();
  const root = roots.find((r) => r.id === target.rootId);
  if (!root) return null;

  const conv = getConversations().find((c) => c.id === conversationId);
  const label = conv?.title || root.name || "工作区";
  return createLocalRootSource(target.rootId, label, target.subpath);
}
