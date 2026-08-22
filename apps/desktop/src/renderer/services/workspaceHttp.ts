import { hasNativeSave } from "@/lib/capabilities";
import { logEvent } from "@/lib/log";
import { bearerAuthHeader, sessionCredentials } from "@/lib/sessionAuth";
import {
  ApiError,
  NetworkError,
  captureCsrf,
  getCsrfHeaders,
  isReplayableCsrfRejection,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/**
 * Neutral HTTP primitives + wire types shared by every workspace/file REST client
 * (文件中枢统一 §二). These are addressing-agnostic: the conversation-scoped client
 * (`services/workspace`), the ws-id-scoped client (`services/workspaces`), the 消息
 * chat-files client (`services/messaging`) and conversation export
 * (`services/conversations`) all build their own URLs and reuse these for the
 * cross-cutting concerns — cookie auth + one-shot recovery, blob save, path encoding,
 * and the binary/too-large preview decode. Kept here (not in any one scoped client)
 * so no scoped module depends on a sibling just to borrow a helper.
 */

/** Encode a workspace-relative path for a `{path:path}` route (keep slashes). */
export function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

/**
 * Fetch with the app's cookie auth + one-shot recovery policy, for the raw-bytes
 * endpoints (upload/download/zip) that bypass the JSON `api` helper. Mirrors
 * `api.request`'s 401→refresh→replay **and** its CSRF-403 replay, off the same
 * exported verdict: without the latter, a missing or rotated CSRF token has every
 * `api.*` write self-heal while attachment uploads alone hard-fail, which reads
 * to the user as "everything works except this one button".
 *
 * Deliberately *not* mirrored: a 5xx here does not trip the availability gate.
 * One failed file preview must not drag the whole app onto the offline retry
 * screen — the asymmetry is the point.
 */
export async function authedFetch(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  // Headers are built per attempt, never hoisted: both recoveries below land
  // after the CSRF token moved (the 403 hands back a replacement; a refresh
  // rotates it), so a replay reusing the rejected attempt's headers would just
  // present the token the server has already replaced.
  const send = async (): Promise<Response> => {
    let res: Response;
    try {
      res = await fetch(url, {
        ...init,
        credentials: sessionCredentials(),
        headers: {
          ...bearerAuthHeader(),
          ...getCsrfHeaders(method),
          ...init.headers,
        },
      });
    } catch (cause) {
      throw new NetworkError(cause);
    }
    captureCsrf(res); // token rides every response — never read the body only
    return res;
  };

  let res = await send();
  // One replay for the whole call — the same bound as `request`'s `retry` flag,
  // so a server that keeps rejecting costs one extra attempt, never a loop.
  let replayed = false;

  if (res.status === 401) {
    const outcome = await tryRefresh();
    if (outcome === "renewed") {
      res = await send();
      replayed = true;
      // Refused again while holding a token the server just minted: the session
      // really is gone, and nothing else would say so. `describeError` maps every
      // 401 to `null` — deliberately, because auth failures are supposed to
      // redirect — so the toast stays silent too, and this branch is exactly the
      // silent black hole the rest of this function exists to close.
      if (res.status === 401) {
        notifyUnauthorized({
          reason: "replay_still_401",
          via: "workspace_http",
        });
      }
    } else if (outcome === "auth_dead") {
      // Otherwise the session dies right here in silence: no login redirect, no
      // prompt, just a download that failed.
      notifyUnauthorized({
        reason: "refresh_auth_dead",
        via: "workspace_http",
      });
    }
    // `transient` falls through to the ApiError below — a flaky refresh must
    // never read as session death.
  }

  if (res.ok) return res;

  const error = new ApiError(res.status, await res.text(), res.headers);
  if (replayed || !isReplayableCsrfRejection(res, error)) throw error;

  logEvent("info", "auth.csrf_replay", { method, via: "workspace_http" });
  res = await send();
  if (res.ok) return res;
  throw new ApiError(res.status, await res.text(), res.headers);
}

/**
 * blob: URL 的延迟回收窗口。click 后**同步** revoke 属规范竞态——下载导航按规范在
 * 异步 fetch 时才解析 blob URL entry（现代 Chromium/Firefox 于导航启动时快照、实测
 * 侥幸不炸，但这不是可依赖的保证，Safari/旧引擎行为不同）。参照 FileSaver.js 延迟
 * 回收，窗口足够任何引擎把下载启动；之后回收避免长会话累积泄漏。
 */
const REVOKE_DELAY_MS = 60_000;

/**
 * Save a blob to the user's disk — the single seam every download goes through
 * (云工作区文件 / 快照 zip / 对话导出 / IM 附件 / 图表·白板导出)。
 *
 * 桌面（Electron）：经 `fs:saveFile` IPC 交主进程弹「另存为」对话框 + 原子落盘。
 * Electron 不支持 `<a download>` + blob:（不触发 will-download，且 blob: 导航被
 * will-navigate 安全守卫拦截 → 打包端点击「无反应」的根因），主进程落盘是根治；
 * **不**放宽 will-navigate 放行 blob:。
 *
 * web（浏览器运行时）：object-URL anchor 下载，revoke 延迟到下载启动之后。
 *
 * 用户在保存对话框里取消 → 正常 resolve（主动放弃非错误，不该弹错误提示）；
 * 真实失败（写盘/IPC 错误）→ reject，由各下载入口 toast。
 */
export async function saveBlob(blob: Blob, filename: string): Promise<void> {
  const name = filename || "download";
  if (hasNativeSave()) {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const result = await window.fsApi.saveFile(name, bytes);
    if (!result.ok && result.reason === "error") {
      throw new Error(result.message || "保存文件失败");
    }
    return;
  }
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = name;
  document.body.appendChild(a);
  try {
    a.click();
  } finally {
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), REVOKE_DELAY_MS);
  }
}

/** A workspace entry (file or directory) keyed by its workspace-relative path. */
export interface WorkspaceFile {
  /** Workspace-relative POSIX path. */
  path: string;
  isDir: boolean;
  /** Byte size; null for directories and for entries the server could not stat. */
  sizeBytes: number | null;
  /** Last-modified epoch ms (same clock as the edit CAS baseline); null when unknown. */
  mtimeMs: number | null;
}

/** Wire entry → {@link WorkspaceFile} (会话 / ws-id 两个客户端同一口径)。 */
export function toWorkspaceFile(
  e: Schemas["WorkspaceFileEntry"],
): WorkspaceFile {
  return {
    path: e.path,
    isDir: e.is_dir,
    sizeBytes: e.size_bytes ?? null,
    mtimeMs: e.mtime_ms ?? null,
  };
}

/**
 * 一次工作区列举的结果：条目 + 是否被服务端条数上限截断。
 *
 * `truncated` 必须一路带到 UI——被悄悄砍掉的树在用户眼里就是「我的文件没了」，
 * 上限可以存在，但必须说出来。
 */
export interface WorkspaceListing {
  files: WorkspaceFile[];
  truncated: boolean;
}

/** `?recursive=&path=` for the two `/files` listing clients (会话 / ws-id 同一口径)。 */
export function listQuery(opts: {
  recursive?: boolean;
  dir?: string;
}): string {
  const params = new URLSearchParams({
    recursive: String(opts.recursive ?? false),
  });
  if (opts.dir) params.set("path", opts.dir);
  return params.toString();
}

/**
 * In-panel text display truncate — aligned with desktop IPC `TEXT_PREVIEW_CAP`
 * (256 KiB). Cloud still fetches up to {@link PREVIEW_HARD_BYTES} then slices.
 */
const PREVIEW_MAX_BYTES = 256 * 1024;
/**
 * Cloud-only network hard cap for non-image / non-PDF preview (no Range; whole-body fetch).
 * Local IPC has no peer — intentional asymmetry.
 */
const PREVIEW_HARD_BYTES = 5 * 1024 * 1024;
/** Inline image preview cap — aligned with desktop IPC `IMAGE_PREVIEW_CAP`. */
const IMAGE_PREVIEW_CAP = 10 * 1024 * 1024;
/**
 * Inline PDF preview cap — aligned with desktop IPC `PDF_PREVIEW_CAP`（15 MiB，略高于图帽）。
 */
const PDF_PREVIEW_CAP = 15 * 1024 * 1024;

/** Ext → MIME for when the server falls back to `application/octet-stream`. */
const IMAGE_MIME_BY_EXT: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".bmp": "image/bmp",
  ".ico": "image/x-icon",
  ".avif": "image/avif",
};

const PDF_MIME = "application/pdf";

const OVERSIZE_IMAGE_REASON =
  "图片过大（超过 10MB），请下载或用系统默认程序打开";
const OVERSIZE_PDF_REASON = "PDF 过大（超过 15MB），请下载或用系统默认程序打开";
const BINARY_PREVIEW_REASON = "无法在面板内预览，请下载或用系统默认程序打开";

/**
 * The outcome of a preview read: decodable text (possibly truncated), an inline
 * image / PDF, or a reason it can't be shown inline (binary / too big → download).
 */
export type FilePreview =
  | { kind: "text"; text: string; truncated: boolean }
  | { kind: "image"; dataUrl: string; mime: string; size: number }
  | { kind: "pdf"; dataUrl: string; mime: string; size: number }
  | { kind: "binary"; mime?: string; size?: number; reason?: string }
  | { kind: "too-large" };

function extOfPath(path: string | undefined): string {
  if (!path) return "";
  const base = path.replace(/\\/g, "/").split("/").pop() ?? "";
  const dot = base.lastIndexOf(".");
  return dot >= 0 ? base.slice(dot).toLowerCase() : "";
}

/** Prefer `Content-Type: image/*`; else guess from the workspace-relative path. */
function resolveImageMime(
  contentType: string | null,
  path?: string,
): string | null {
  const ct = (contentType ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
  if (ct.startsWith("image/")) return ct;
  return IMAGE_MIME_BY_EXT[extOfPath(path)] ?? null;
}

/** Prefer `Content-Type: application/pdf`; else `.pdf` path extension. */
function resolvePdfMime(
  contentType: string | null,
  path?: string,
): string | null {
  const ct = (contentType ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
  if (ct === PDF_MIME || ct === "application/x-pdf") return PDF_MIME;
  if (extOfPath(path) === ".pdf") return PDF_MIME;
  return null;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/**
 * Decode a raw file response into an in-panel preview result.
 *
 * Images / PDFs use `Content-Type` / path extension → inline data URL, matching
 * local IPC. Other bodies are UTF-8–probed; NUL / high replacement ratio
 * → binary. Shared by conversation-scoped and ws-id-scoped preview reads.
 */
export async function decodePreviewResponse(
  res: Response,
  opts?: { path?: string },
): Promise<FilePreview> {
  const declared = Number(res.headers.get("content-length") ?? "0");
  const contentType = res.headers.get("content-type");
  const imageMime = resolveImageMime(contentType, opts?.path);

  if (imageMime) {
    if (declared > IMAGE_PREVIEW_CAP) {
      return {
        kind: "binary",
        mime: imageMime,
        size: declared,
        reason: OVERSIZE_IMAGE_REASON,
      };
    }
    const bytes = new Uint8Array(await res.arrayBuffer());
    if (bytes.length > IMAGE_PREVIEW_CAP) {
      return {
        kind: "binary",
        mime: imageMime,
        size: bytes.length,
        reason: OVERSIZE_IMAGE_REASON,
      };
    }
    return {
      kind: "image",
      dataUrl: `data:${imageMime};base64,${bytesToBase64(bytes)}`,
      mime: imageMime,
      size: bytes.length,
    };
  }

  const pdfMime = resolvePdfMime(contentType, opts?.path);
  if (pdfMime) {
    if (declared > PDF_PREVIEW_CAP) {
      return {
        kind: "binary",
        mime: pdfMime,
        size: declared,
        reason: OVERSIZE_PDF_REASON,
      };
    }
    const bytes = new Uint8Array(await res.arrayBuffer());
    if (bytes.length > PDF_PREVIEW_CAP) {
      return {
        kind: "binary",
        mime: pdfMime,
        size: bytes.length,
        reason: OVERSIZE_PDF_REASON,
      };
    }
    return {
      kind: "pdf",
      dataUrl: `data:${pdfMime};base64,${bytesToBase64(bytes)}`,
      mime: pdfMime,
      size: bytes.length,
    };
  }

  if (declared > PREVIEW_HARD_BYTES) return { kind: "too-large" };

  const bytes = new Uint8Array(await res.arrayBuffer());
  if (bytes.length > PREVIEW_HARD_BYTES) return { kind: "too-large" };

  const truncated = bytes.length > PREVIEW_MAX_BYTES;
  const slice = truncated ? bytes.subarray(0, PREVIEW_MAX_BYTES) : bytes;

  const probe = Math.min(slice.length, 8192);
  for (let i = 0; i < probe; i++) {
    if (slice[i] === 0) {
      return { kind: "binary", reason: BINARY_PREVIEW_REASON };
    }
  }

  const text = new TextDecoder("utf-8", { fatal: false }).decode(slice);
  const scan = Math.min(text.length, 4096);
  let replacements = 0;
  for (let i = 0; i < scan; i++) {
    if (text.charCodeAt(i) === 0xfffd) replacements++;
  }
  if (scan > 0 && replacements / scan > 0.1) {
    return { kind: "binary", reason: BINARY_PREVIEW_REASON };
  }

  return { kind: "text", text, truncated };
}

/** Full text + CAS baseline (mtime) for editing a cloud-workspace file. */
export interface WorkspaceEditDoc {
  text: string;
  mtimeMs: number;
  eol: "lf" | "crlf";
}

/** A conditional write's outcome: `ok` → new version; otherwise a conflict whose
 * `mtimeMs` is the current **disk** version (re-write with it to overwrite). */
export interface WorkspaceWriteOutcome {
  ok: boolean;
  mtimeMs: number;
  conflict: boolean;
}

// --- Snapshots / AgentCore-trash wire shapes (会话 · ws-id 两个客户端共用) ---

/** One persisted workspace snapshot (a kept version, or an automatic backup). */
export interface WorkspaceSnapshot {
  snapshotId: string;
  /** A user-pinned name (手动留版本), or null for an automatic post-turn backup. */
  label: string | null;
  createdAt: string;
  sizeBytes: number;
}

export function toSnapshot(s: Schemas["SnapshotSummary"]): WorkspaceSnapshot {
  return {
    snapshotId: s.snapshot_id,
    label: s.label,
    createdAt: s.created_at,
    sizeBytes: s.size_bytes,
  };
}

/** One reversible soft-delete under ``AgentCore/trash`` (not the OS recycle bin). */
export interface WorkspaceTrashEntry {
  entryId: string;
  originalPath: string;
  name: string;
  isDir: boolean;
  deletedAt: string;
}

export function toTrashEntry(
  e: Schemas["TrashEntrySummary"],
): WorkspaceTrashEntry {
  return {
    entryId: e.entry_id,
    originalPath: e.original_path,
    name: e.name,
    isDir: e.is_dir,
    deletedAt: e.deleted_at,
  };
}

/** blob → base64（分块，避免大文件撑爆调用栈）。 */
export async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}
