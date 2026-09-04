import { BASE_URL, api } from "@/services/api";
import {
  type FilePreview,
  type WorkspaceEditDoc,
  type WorkspaceListing,
  type WorkspaceSnapshot,
  type WorkspaceTrashEntry,
  type WorkspaceWriteOutcome,
  authedFetch,
  blobToBase64,
  decodePreviewResponse,
  encodePath,
  listQuery,
  saveBlob,
  toSnapshot,
  toTrashEntry,
  toWorkspaceFile,
} from "@/services/workspaceHttp";
import type { components } from "@/types/api.generated";

export type { WorkspaceSnapshot, WorkspaceTrashEntry };

type Schemas = components["schemas"];

/**
 * The first-class workspace REST client, addressed by **workspace id** (文件中枢
 * 统一 Step 1/2): `ws_id = "conv:<conversationId>"` (primary). This backs the file hub,
 * which browses *conversation scratch spaces* — distinct from `services/workspace` (the
 * per-conversation alias kept for the chat panel). Both hit the same server service
 * layer; only the addressing differs. File/CRUD here are valid for **cloud**
 * workspaces — local ones are reached over desktop IPC (the server returns 409),
 * so the hub picks `LocalRootSource` for those (§五).
 */

export interface WorkspaceInfo {
  wsId: string;
  name: string;
  location: "cloud" | "local";
  /** The bound desktop root id when local; null when cloud. */
  rootId: string | null;
  /** Sub-path within the bound local root (工作区对称化 D1a); "" = the root itself
   * (an explicitly-added local project) or cloud. A non-empty segment marks a
   * per-conversation workspace lazily promoted under a shared container root —
   * the hub scopes its browse ops to this subtree. */
  subpath: string;
  hasFiles: boolean;
}

/** Enumerate the user's workspaces for the hub rail. */
export async function listWorkspaces(): Promise<WorkspaceInfo[]> {
  const res = await api.get<Schemas["WorkspaceListResponse"]>("/v1/workspaces");
  return res.data.map((w) => ({
    wsId: w.ws_id,
    name: w.name,
    location: w.location,
    rootId: w.root_id ?? null,
    subpath: w.subpath ?? "",
    hasFiles: w.has_files,
  }));
}

const wsPath = (wsId: string): string =>
  `/v1/workspaces/${encodeURIComponent(wsId)}`;
const wsUrl = (wsId: string): string => `${BASE_URL}${wsPath(wsId)}`;

/**
 * List one directory of a workspace (`dir` omitted / `"."` = root).
 *
 * Recursive pulls the whole subtree in one call; either way the server's entry
 * ceiling can bite, so the caller gets `truncated` alongside the entries.
 */
export async function wsListFiles(
  wsId: string,
  opts: { recursive?: boolean; dir?: string } = {},
): Promise<WorkspaceListing> {
  const res = await api.get<Schemas["WorkspaceFileListResponse"]>(
    `${wsPath(wsId)}/files?${listQuery(opts)}`,
  );
  return {
    files: res.data.map(toWorkspaceFile),
    truncated: res.truncated ?? false,
  };
}

/**
 * Flat file-path list for @ mentions (文件中枢统一 F4). Files only, ignore-pruned,
 * capped server-side — the cloud counterpart to `fsApi.listFiles` over a local
 * root, so @ indexes cloud and local workspaces the same way. Cloud-only (the
 * server refuses local workspaces with 409). `truncated` 与本地索引同一形状透出。
 */
export async function wsListFileIndex(wsId: string): Promise<{
  files: Array<{ relPath: string }>;
  truncated: boolean;
}> {
  const res = await api.get<Schemas["WorkspaceFileIndexResponse"]>(
    `${wsPath(wsId)}/file-index`,
  );
  return {
    files: res.data.map((relPath) => ({ relPath })),
    truncated: res.truncated ?? false,
  };
}

/** Upload (create/overwrite) a workspace file from raw bytes. */
export async function wsUploadFile(
  wsId: string,
  path: string,
  body: Blob,
): Promise<void> {
  await authedFetch(`${wsUrl(wsId)}/files/${encodePath(path)}`, {
    method: "PUT",
    body,
  });
}

/** Export a workspace Markdown file to a sibling ``.docx`` (server converter). */
export async function wsExportMdToDocx(
  wsId: string,
  path: string,
): Promise<{ path: string; warnings: string[] }> {
  const res = await api.post<{
    path: string;
    source_path: string;
    size_bytes: number;
    warnings: string[];
  }>(`${wsPath(wsId)}/export-docx`, { path });
  return { path: res.path, warnings: res.warnings ?? [] };
}

/** Stateless Markdown → Word (local desktop path; images as base64). */
export async function convertMdToDocx(input: {
  markdown: string;
  images: Record<string, string | null>;
  sourceName: string;
}): Promise<{
  docxBase64: string;
  warnings: string[];
  suggestedFilename: string;
}> {
  const res = await api.post<{
    docx_base64: string;
    warnings: string[];
    suggested_filename: string;
  }>("/v1/workspaces/convert/md-to-docx", {
    markdown: input.markdown,
    images: input.images,
    source_name: input.sourceName,
  });
  return {
    docxBase64: res.docx_base64,
    warnings: res.warnings ?? [],
    suggestedFilename: res.suggested_filename,
  };
}

/** Delete a workspace file or directory (directories go recursively). */
export async function wsDeleteFile(wsId: string, path: string): Promise<void> {
  await api.delete(`${wsPath(wsId)}/files/${encodePath(path)}`);
}

/** Move/rename a workspace file or directory (`AlreadyExists` → 422). */
export async function wsMoveFile(
  wsId: string,
  src: string,
  dst: string,
): Promise<void> {
  await api.post(`${wsPath(wsId)}/move`, { src, dst });
}

/** Copy a workspace file or directory tree (`AlreadyExists` → 422). */
export async function wsCopyFile(
  wsId: string,
  src: string,
  dst: string,
): Promise<void> {
  await api.post(`${wsPath(wsId)}/copy`, { src, dst });
}

/** Create a workspace directory (parents created; `AlreadyExists` → 422). */
export async function wsCreateDir(wsId: string, path: string): Promise<void> {
  await api.post(`${wsPath(wsId)}/dirs`, { path });
}

/** Download a workspace file and save it via the browser. */
export async function wsDownloadFile(
  wsId: string,
  path: string,
  filename: string,
): Promise<void> {
  const res = await authedFetch(`${wsUrl(wsId)}/files/${encodePath(path)}`);
  await saveBlob(await res.blob(), filename);
}

/**
 * Download a workspace directory as zip (selected dir as archive root).
 *
 * Independent of {@link wsDownloadFile}. Not {@link wsExportZip} (that still
 * snapshots the whole desk then downloads the snapshot).
 */
export async function wsDownloadArchive(
  wsId: string,
  path: string,
  filename: string,
): Promise<void> {
  const res = await authedFetch(`${wsUrl(wsId)}/archive/${encodePath(path)}`);
  await saveBlob(await res.blob(), filename);
}

/**
 * Fetch a workspace file as a Blob — the raw-bytes twin of {@link wsDownloadFile}
 * (which routes the same bytes into a save dialog). Mirrors
 * `fetchWorkspaceFileBlob` on the conversation-keyed client; only addressing differs.
 */
export async function wsFetchFileBlob(
  wsId: string,
  path: string,
): Promise<Blob> {
  const res = await authedFetch(`${wsUrl(wsId)}/files/${encodePath(path)}`);
  return res.blob();
}

/** Read a workspace file for read-only in-panel preview. */
export async function wsReadFile(
  wsId: string,
  path: string,
): Promise<FilePreview> {
  const res = await authedFetch(`${wsUrl(wsId)}/files/${encodePath(path)}`);
  return decodePreviewResponse(res, { path });
}

// --- Snapshots · AgentCore/trash, addressed by ws id ---
//
// The ws-id twins of the conversation-scoped calls in `services/workspace`: same
// server service layer, same storage key, only the addressing differs. They exist
// because the file hub browses workspaces without a conversation in hand — without
// them 版本 / 软删区 / 导出 are only reachable by detouring through some chat's side
// dock. The server refuses these (409) for local workspaces (files live on the
// user's machine), so callers gate the entry points instead of letting the user
// click into a 409. Cloud desks are `folder:` / `conv:` only.

/** Server snapshot payload (`/v1/workspaces/{ws_id}/snapshots`). */
type BackendSnapshot = Schemas["SnapshotSummary"];

/** List a workspace's snapshots (newest first). */
export async function wsListSnapshots(
  wsId: string,
): Promise<WorkspaceSnapshot[]> {
  const res = await api.get<Schemas["SnapshotListResponse"]>(
    `${wsPath(wsId)}/snapshots`,
  );
  return res.data.map(toSnapshot);
}

/** Take a manual snapshot of a cloud workspace addressed by ws id. */
export async function wsCreateSnapshot(
  wsId: string,
  label?: string,
): Promise<WorkspaceSnapshot> {
  const res = await api.post<BackendSnapshot>(`${wsPath(wsId)}/snapshots`, {
    label: label?.trim() || null,
  } satisfies Schemas["CreateSnapshotRequest"]);
  return toSnapshot(res);
}

/** Restore a cloud workspace to a snapshot (overlay over the current files). */
export async function wsRestoreSnapshot(
  wsId: string,
  snapshotId: string,
): Promise<void> {
  await api.post(
    `${wsPath(wsId)}/snapshots/${encodeURIComponent(snapshotId)}/restore`,
  );
}

/** Download a snapshot's zip archive and save it via the browser. */
export async function wsDownloadSnapshot(
  wsId: string,
  snapshotId: string,
): Promise<void> {
  const res = await authedFetch(
    `${wsUrl(wsId)}/snapshots/${encodeURIComponent(snapshotId)}/download`,
  );
  await saveBlob(await res.blob(), `workspace-${snapshotId}.zip`);
}

/** Snapshot the workspace's current files and download the lot as one zip. */
export async function wsExportZip(wsId: string): Promise<void> {
  const snap = await wsCreateSnapshot(wsId, "导出");
  await wsDownloadSnapshot(wsId, snap.snapshotId);
}

/** List AgentCore/trash for a cloud workspace (newest first). */
export async function wsListTrash(
  wsId: string,
): Promise<{ entries: WorkspaceTrashEntry[]; retentionDays: number }> {
  const res = await api.get<Schemas["TrashListResponse"]>(
    `${wsPath(wsId)}/trash`,
  );
  return {
    entries: res.data.map(toTrashEntry),
    retentionDays: res.retention_days,
  };
}

/** Restore one AgentCore/trash entry to its original relative path. */
export async function wsRestoreTrash(
  wsId: string,
  entryId: string,
): Promise<void> {
  await api.post(
    `${wsPath(wsId)}/trash/${encodeURIComponent(entryId)}/restore`,
  );
}

/**
 * 「在浏览器打开」文件中枢云端工作区 HTML：ws 快照 → zip → 主进程解压临时目录 →
 * 系统默认浏览器。仅 `folder:` / `conv:` 云桌（快照寻址）。Desktop-only
 * (`previewArchive`)。
 */
export async function openCloudWorkspaceInBrowser(
  wsId: string,
  htmlPath: string,
): Promise<void> {
  const preview = window.fsApi?.previewArchive;
  if (!preview) throw new Error("此环境不支持在浏览器打开");
  const snap = await wsCreateSnapshot(wsId, "浏览器预览");
  const res = await authedFetch(
    `${wsUrl(wsId)}/snapshots/${encodeURIComponent(snap.snapshotId)}/download`,
  );
  const archiveBase64 = await blobToBase64(await res.blob());
  const result = await preview(archiveBase64, htmlPath);
  if (!result.ok) throw new Error(result.message);
}

/** Read a cloud-workspace file for **editing** (full text + mtime CAS baseline). */
export async function wsReadFileForEdit(
  wsId: string,
  path: string,
): Promise<WorkspaceEditDoc> {
  const res = await api.get<Schemas["WorkspaceEditDoc"]>(
    `${wsPath(wsId)}/edit/${encodePath(path)}`,
  );
  return { text: res.text, mtimeMs: res.mtime_ms, eol: res.eol };
}

/** Conditionally write editor text back (mtime CAS); conflict carries disk mtime. */
export async function wsWriteFileText(
  wsId: string,
  path: string,
  input: { content: string; eol: "lf" | "crlf"; baselineMtimeMs: number },
): Promise<WorkspaceWriteOutcome> {
  const res = await api.put<Schemas["WorkspaceWriteResult"]>(
    `${wsPath(wsId)}/edit/${encodePath(path)}`,
    {
      content: input.content,
      eol: input.eol,
      baseline_mtime_ms: input.baselineMtimeMs,
    } satisfies Schemas["WorkspaceWriteRequest"],
  );
  return { ok: res.ok, mtimeMs: res.mtime_ms, conflict: res.conflict };
}

/** Shallow-clone an http(s) repo into a cloud workspace (G3). */
export async function wsCloneRepo(
  wsId: string,
  input: { repoUrl: string; dest?: string | null },
): Promise<string> {
  const res = await api.post<Schemas["CloneRepoResponse"]>(
    `${wsPath(wsId)}/clone`,
    {
      repo_url: input.repoUrl,
      dest: input.dest ?? null,
    } satisfies Schemas["CloneRepoRequest"],
  );
  return res.path;
}
