import { hasLocalFiles } from "@/lib/capabilities";
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

// Snapshot / trash shapes moved to the neutral wire module (both clients speak
// them); re-exported so existing `@/services/workspace` importers keep working.
export type { WorkspaceSnapshot, WorkspaceTrashEntry };

type Schemas = components["schemas"];

/**
 * Conversation-scoped workspace REST client — the per-conversation alias used by
 * the chat panel: every op hits `/v1/conversations/{id}/workspace/...`. The file
 * hub instead addresses workspaces by id via `services/workspaces`; both share the
 * neutral primitives in `services/workspaceHttp` and hit the same server service
 * layer, only the addressing differs. Snapshots live here because they are a
 * conversation-scoped concern with no ws-id counterpart.
 */

const filesBase = (conversationId: string): string =>
  `${BASE_URL}/v1/conversations/${conversationId}/workspace/files`;
const archiveBase = (conversationId: string): string =>
  `${BASE_URL}/v1/conversations/${conversationId}/workspace/archive`;

// --- Files (bring files in / take results out: 文件进出) ---

/**
 * List one directory of the conversation's workspace (`dir` omitted / `"."` = root).
 *
 * Recursive pulls the whole subtree in one call; either way the server's entry
 * ceiling can bite, so the caller gets `truncated` alongside the entries.
 */
export async function listWorkspaceFiles(
  conversationId: string,
  opts: { recursive?: boolean; dir?: string } = {},
): Promise<WorkspaceListing> {
  const res = await api.get<Schemas["WorkspaceFileListResponse"]>(
    `/v1/conversations/${conversationId}/workspace/files?${listQuery(opts)}`,
  );
  return {
    files: res.data.map(toWorkspaceFile),
    truncated: res.truncated ?? false,
  };
}

/** Upload (create/overwrite) a workspace file from raw bytes. */
export async function uploadWorkspaceFile(
  conversationId: string,
  path: string,
  body: Blob,
): Promise<void> {
  await authedFetch(`${filesBase(conversationId)}/${encodePath(path)}`, {
    method: "PUT",
    body,
  });
}

/** Export a conversation-workspace Markdown file to a sibling ``.docx``. */
export async function exportWorkspaceMdToDocx(
  conversationId: string,
  path: string,
): Promise<{ path: string; warnings: string[] }> {
  const res = await api.post<{
    path: string;
    source_path: string;
    size_bytes: number;
    warnings: string[];
  }>(`/v1/conversations/${conversationId}/workspace/export-docx`, { path });
  return { path: res.path, warnings: res.warnings ?? [] };
}

/** Delete a workspace file or directory (directories go recursively). */
export async function deleteWorkspaceFile(
  conversationId: string,
  path: string,
): Promise<void> {
  await api.delete(
    `/v1/conversations/${conversationId}/workspace/files/${encodePath(path)}`,
  );
}

/** Move/rename a workspace file or directory (`AlreadyExists` → 422). */
export async function moveWorkspaceFile(
  conversationId: string,
  src: string,
  dst: string,
): Promise<void> {
  await api.post(`/v1/conversations/${conversationId}/workspace/move`, {
    src,
    dst,
  });
}

/** Copy a workspace file or directory tree (`AlreadyExists` → 422). */
export async function copyWorkspaceFile(
  conversationId: string,
  src: string,
  dst: string,
): Promise<void> {
  await api.post(`/v1/conversations/${conversationId}/workspace/copy`, {
    src,
    dst,
  });
}

/** Create a workspace directory (parents created; `AlreadyExists` → 422). */
export async function createWorkspaceDir(
  conversationId: string,
  path: string,
): Promise<void> {
  await api.post(`/v1/conversations/${conversationId}/workspace/dirs`, {
    path,
  });
}

/**
 * Download a file from a conversation's workspace and save it via the browser.
 *
 * The file API is JSON-less (raw bytes), so this fetches directly (reusing the
 * shared cookie auth + refresh-once) and triggers a save through an object-URL
 * anchor. Backs both the resident-attachment chip (附件驻留) and the workspace
 * panel's per-file download.
 */
export async function downloadWorkspaceFile(
  conversationId: string,
  workspacePath: string,
  filename: string,
): Promise<void> {
  const res = await authedFetch(
    `${filesBase(conversationId)}/${encodePath(workspacePath)}`,
  );
  await saveBlob(await res.blob(), filename);
}

/**
 * Download a conversation-workspace directory as zip (selected dir as archive root).
 *
 * Independent of {@link downloadWorkspaceFile} — GET `.../files/{path}` stays
 * preview / single-file. Root「导出 ZIP」is still snapshot create+download.
 */
export async function downloadWorkspaceArchive(
  conversationId: string,
  workspacePath: string,
  filename: string,
): Promise<void> {
  const res = await authedFetch(
    `${archiveBase(conversationId)}/${encodePath(workspacePath)}`,
  );
  await saveBlob(await res.blob(), filename);
}

/**
 * Fetch a conversation-workspace file as a Blob (for inline rendering via an object
 * URL) — the raw-bytes twin of {@link downloadWorkspaceFile}, reusing the shared
 * cookie auth + refresh-once. Backs the 团队浏览器活动卡 key-frame lazy load (mirrors
 * the IM `fetchChatAttachmentBlob` blob + objectURL pattern, only the addressing —
 * conversation workspace vs chat space — differs).
 */
export async function fetchWorkspaceFileBlob(
  conversationId: string,
  workspacePath: string,
): Promise<Blob> {
  const res = await authedFetch(
    `${filesBase(conversationId)}/${encodePath(workspacePath)}`,
  );
  return res.blob();
}

/** Read a conversation-workspace file for read-only in-panel preview. */
export async function readWorkspaceFile(
  conversationId: string,
  path: string,
): Promise<FilePreview> {
  const res = await authedFetch(
    `${filesBase(conversationId)}/${encodePath(path)}`,
  );
  return decodePreviewResponse(res, { path });
}

// --- Edit (源无关编辑契约的云端实现: full text + mtime CAS) ---

/**
 * Read a conversation-workspace file for **editing** — full text (never truncated,
 * unlike preview) + the mtime baseline a later save does its CAS against. The
 * editable counterpart of {@link readWorkspaceFile}.
 */
export async function readWorkspaceFileForEdit(
  conversationId: string,
  path: string,
): Promise<WorkspaceEditDoc> {
  const res = await api.get<Schemas["WorkspaceEditDoc"]>(
    `/v1/conversations/${conversationId}/workspace/edit/${encodePath(path)}`,
  );
  return { text: res.text, mtimeMs: res.mtime_ms, eol: res.eol };
}

/**
 * Conditionally write editor text back (mtime CAS). A `conflict` (disk changed
 * since `baselineMtimeMs`, e.g. an Agent turn wrote it) returns `ok:false` with the
 * disk mtime instead of clobbering — never a blind overwrite.
 */
export async function writeWorkspaceFileText(
  conversationId: string,
  path: string,
  input: { content: string; eol: "lf" | "crlf"; baselineMtimeMs: number },
): Promise<WorkspaceWriteOutcome> {
  const res = await api.put<Schemas["WorkspaceWriteResult"]>(
    `/v1/conversations/${conversationId}/workspace/edit/${encodePath(path)}`,
    {
      content: input.content,
      eol: input.eol,
      baseline_mtime_ms: input.baselineMtimeMs,
    } satisfies Schemas["WorkspaceWriteRequest"],
  );
  return { ok: res.ok, mtimeMs: res.mtime_ms, conflict: res.conflict };
}

// --- Snapshots (axis-3 persistence: backup / kept versions / download) ---

/** Server snapshot payload (`/snapshots`), generated from OpenAPI. */
type BackendSnapshot = Schemas["SnapshotSummary"];

/** List the conversation's workspace snapshots (newest first). */
export async function listSnapshots(
  conversationId: string,
): Promise<WorkspaceSnapshot[]> {
  const res = await api.get<Schemas["SnapshotListResponse"]>(
    `/v1/conversations/${conversationId}/snapshots`,
  );
  return res.data.map(toSnapshot);
}

/** Take a manual snapshot; a non-empty `label` keeps it as a named version. */
export async function createSnapshot(
  conversationId: string,
  label?: string,
): Promise<WorkspaceSnapshot> {
  const res = await api.post<BackendSnapshot>(
    `/v1/conversations/${conversationId}/snapshots`,
    { label: label?.trim() || null },
  );
  return toSnapshot(res);
}

/** Restore the workspace to a snapshot (overwrites current files). */
export async function restoreSnapshot(
  conversationId: string,
  snapshotId: string,
): Promise<void> {
  await api.post(
    `/v1/conversations/${conversationId}/snapshots/${snapshotId}/restore`,
  );
}

// --- AgentCore/trash (soft-delete restore; not OS recycle bin) ---

/** List AgentCore/trash for a cloud conversation workspace (newest first). */
export async function listTrash(
  conversationId: string,
): Promise<{ entries: WorkspaceTrashEntry[]; retentionDays: number }> {
  const res = await api.get<Schemas["TrashListResponse"]>(
    `/v1/conversations/${conversationId}/trash`,
  );
  return {
    entries: res.data.map(toTrashEntry),
    retentionDays: res.retention_days,
  };
}

/** Restore one AgentCore/trash entry to its original relative path. */
export async function restoreTrash(
  conversationId: string,
  entryId: string,
): Promise<void> {
  await api.post(
    `/v1/conversations/${conversationId}/trash/${entryId}/restore`,
  );
}

/** Download a snapshot's zip archive and save it via the browser. */
export async function downloadSnapshot(
  conversationId: string,
  snapshotId: string,
): Promise<void> {
  const res = await authedFetch(
    `${BASE_URL}/v1/conversations/${conversationId}/snapshots/${snapshotId}/download`,
  );
  await saveBlob(await res.blob(), `workspace-${snapshotId}.zip`);
}

/** Snapshot current cloud workspace files and download as a zip (产物导出 · web / 兜底). */
export async function exportWorkspaceZip(
  conversationId: string,
): Promise<void> {
  const snap = await createSnapshot(conversationId, "导出");
  await downloadSnapshot(conversationId, snap.snapshotId);
}

export type ExportWorkspaceToLocalResult =
  | { ok: true; destName: string; fileCount: number }
  | { ok: false; reason: "cancelled" }
  | { ok: false; reason: "unavailable" }
  | { ok: false; reason: "error"; message: string };

/**
 * 云 scratch → 本机单向 checkout（§八.7 / §7.6）：快照 → 用户选目录解压落地。
 * 不必登记合回落点；合回落点写出走 Diff / 只合回产物。非桌面 → unavailable。
 */
export async function exportWorkspaceToLocal(
  conversationId: string,
): Promise<ExportWorkspaceToLocalResult> {
  if (!hasLocalFiles() || !window.fsApi?.checkoutArchive) {
    return { ok: false, reason: "unavailable" };
  }
  const snap = await createSnapshot(conversationId, "导出到本地");
  const res = await authedFetch(
    `${BASE_URL}/v1/conversations/${conversationId}/snapshots/${snap.snapshotId}/download`,
  );
  const archiveBase64 = await blobToBase64(await res.blob());
  return window.fsApi.checkoutArchive(archiveBase64);
}

/**
 * 「在浏览器打开」云端工作区里的某个页面：快照 → 下载 zip → 主进程解压临时目录 →
 * 系统默认浏览器打开该文件。真实浏览器 = 完整 JS + 多文件相对资源（面板内只显示
 * 源码，效果一律出面板看）。桌面专属；失败抛异常（供 UI toast）。
 */
export async function openWorkspaceInBrowser(
  conversationId: string,
  htmlPath: string,
): Promise<void> {
  const preview = window.fsApi?.previewArchive;
  if (!preview) throw new Error("此环境不支持在浏览器打开");
  const snap = await createSnapshot(conversationId, "浏览器预览");
  const res = await authedFetch(
    `${BASE_URL}/v1/conversations/${conversationId}/snapshots/${snap.snapshotId}/download`,
  );
  const archiveBase64 = await blobToBase64(await res.blob());
  const result = await preview(archiveBase64, htmlPath);
  if (!result.ok) throw new Error(result.message);
}
