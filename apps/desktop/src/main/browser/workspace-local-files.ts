/**
 * 本机引擎工作区根 → `workspace://` 字节。
 *
 * 云端完整预览不绑定根，协议继续走 `/v1/workspaces/.../files`。
 * 本机 sidecar 把这次 `backend.root` 绑上之后，同一 scheme 读这份磁盘
 * （与 `write` 刚落盘的是同一份），不再打到服务端空目录。
 *
 * 纯 Node，无 electron。路径必须落在已绑定根内（含 realpath，防符号链接逃出）。
 */

import { readFile, realpath, stat } from "node:fs/promises";
import { isAbsolute, relative, resolve } from "node:path";
import { normalizePreviewPath } from "@shared/preview-path";
import { normalizeBrowserConversationId } from "./paths";
import { mimeForPath } from "./workspace-paths";

const roots = new Map<string, string>();
/** `${cid}|${workspaceUrlKey}` → HTTP status of that exact URL. */
const statusByUrl = new Map<string, number>();

export function resetLocalWorkspaceFilesForTests(): void {
  roots.clear();
  statusByUrl.clear();
}

/** 绝对路径才绑定。相对路径 / 空串 / NUL 拒绝，调用方继续走云端取字节。 */
export function bindLocalWorkspaceRoot(
  conversationId: string,
  root: string,
): boolean {
  const cid = normalizeBrowserConversationId(conversationId);
  const abs = typeof root === "string" ? root.trim() : "";
  if (!cid || !abs || abs.includes("\0") || !isAbsolute(abs)) return false;
  roots.set(cid, abs);
  return true;
}

export function clearLocalWorkspaceRoot(conversationId: string): void {
  const cid = normalizeBrowserConversationId(conversationId);
  if (!cid) return;
  roots.delete(cid);
  const prefix = `${cid}|`;
  for (const key of statusByUrl.keys()) {
    if (key.startsWith(prefix)) statusByUrl.delete(key);
  }
}

export function clearAllLocalWorkspaceRoots(): void {
  roots.clear();
  statusByUrl.clear();
}

/**
 * `rel`（已规范化的工作区相对路径）落在 `root` 之下时返回绝对路径。
 * 穿越、盘符跳出、空路径 → null。不解析符号链接；调用方读盘前再 realpath。
 */
export function resolveFileUnderRoot(root: string, rel: string): string | null {
  if (!root.trim() || !rel || rel.includes("\0")) return null;
  if (rel.split(/[\\/]/).includes("..")) return null;
  const base = resolve(root);
  const abs = resolve(base, rel);
  const relTo = relative(base, abs);
  if (!relTo || relTo.startsWith("..") || isAbsolute(relTo)) return null;
  return abs;
}

export type LocalWorkspaceRead =
  | { kind: "unbound" }
  | { kind: "file"; status: 200; body: Uint8Array; mime: string }
  | { kind: "error"; status: 403 | 404 };

/** 未绑定 → `unbound`（协议改走云端）。已绑定但缺失 / 越界 → 403 或 404。 */
export async function readBoundWorkspaceFile(
  conversationId: string,
  rel: string,
): Promise<LocalWorkspaceRead> {
  const cid = normalizeBrowserConversationId(conversationId);
  if (!cid) return { kind: "unbound" };
  const root = roots.get(cid);
  if (!root) return { kind: "unbound" };
  const abs = resolveFileUnderRoot(root, rel);
  if (!abs) return { kind: "error", status: 403 };
  try {
    const realRoot = await realpath(root);
    const realFile = await realpath(abs);
    const relTo = relative(realRoot, realFile);
    if (!relTo || relTo.startsWith("..") || isAbsolute(relTo)) {
      return { kind: "error", status: 403 };
    }
    const info = await stat(realFile);
    if (!info.isFile()) return { kind: "error", status: 404 };
    const body = new Uint8Array(await readFile(realFile));
    return { kind: "file", status: 200, body, mime: mimeForPath(rel) };
  } catch {
    return { kind: "error", status: 404 };
  }
}

/** `workspace://` 文档键（host + 规范化路径）。子资源与主文档分键，互不覆盖。 */
export function workspaceDocumentKey(rawUrl: string): string | null {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  if (url.protocol !== "workspace:") return null;
  const rel = normalizePreviewPath(url.pathname);
  if (!rel) return null;
  return `${url.hostname}/${rel}`;
}

export function noteWorkspaceDocumentStatus(
  conversationId: string,
  requestUrl: string,
  status: number,
): void {
  const cid = normalizeBrowserConversationId(conversationId);
  const key = workspaceDocumentKey(requestUrl);
  if (!cid || !key || !Number.isInteger(status)) return;
  statusByUrl.set(`${cid}|${key}`, status);
}

/** 主文档 URL 对应的协议状态；没有记录 → null（http(s) 用导航事件上的状态码）。 */
export function consumeWorkspaceDocumentStatus(
  conversationId: string,
  pageUrl: string,
): number | null {
  const cid = normalizeBrowserConversationId(conversationId);
  const key = workspaceDocumentKey(pageUrl);
  if (!cid || !key) return null;
  const status = statusByUrl.get(`${cid}|${key}`);
  return status === undefined ? null : status;
}
