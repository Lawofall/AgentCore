/**
 * `workspace://` 自定义协议 —— Local Browser 工作区 HTML 字节来源（L1b）。
 *
 * 请求 `workspace://{folder|conv}.{uuid}/{path}`。
 * 本机会话已绑定引擎工作区根时，读该根目录上的文件（与刚写盘同一份）。
 * 未绑定（云端完整预览）仍 Bearer 代理 `/v1/workspaces/{wsId}/files/{rel}`。
 *
 * 处理器按 **conversation 分区** 注册（`workspacePartitionFor(cid)`）；
 * `conv.*` host 须等于该 partition 绑定的 cid，否则 403；`folder.*` 本 partition 放行。
 */

import { type Session, session } from "electron";
import { bearerFetch } from "../auth-client";
import { normalizeBrowserConversationId } from "./paths";
import {
  noteWorkspaceDocumentStatus,
  readBoundWorkspaceFile,
} from "./workspace-local-files";
import {
  WORKSPACE_CSP,
  WORKSPACE_SCHEME,
  mimeForPath,
  resolveWorkspaceProtocolRequest,
  workspaceFilePath,
  workspacePartitionFor,
} from "./workspace-paths";

/** 已注册协议处理器的 partition 名（幂等）。 */
const registeredPartitions = new Set<string>();

export function workspaceBrowserSessionFor(conversationId: string): Session {
  return session.fromPartition(workspacePartitionFor(conversationId));
}

export { resolveWorkspaceProtocolRequest };

/**
 * 幂等：在指定对话的工作区分区装 `workspace://` 处理器 + 权限全拒。
 * 建 workspace 页前调用。
 */
export function registerWorkspaceProtocolFor(conversationId: string): void {
  const cid = normalizeBrowserConversationId(conversationId);
  if (!cid) return;
  const partition = workspacePartitionFor(cid);
  const sess = session.fromPartition(partition);

  sess.setPermissionRequestHandler((_wc, _permission, callback) =>
    callback(false),
  );
  sess.setPermissionCheckHandler(() => false);

  if (registeredPartitions.has(partition)) return;
  registeredPartitions.add(partition);

  sess.protocol.handle(WORKSPACE_SCHEME, async (request) => {
    const resolved = resolveWorkspaceProtocolRequest(request.url, cid);
    if (!resolved.ok) {
      noteWorkspaceDocumentStatus(cid, request.url, resolved.status);
      return new Response(
        resolved.status === 400 ? "Bad Request" : "Forbidden",
        { status: resolved.status },
      );
    }

    const local = await readBoundWorkspaceFile(cid, resolved.rel);
    if (local.kind !== "unbound") {
      noteWorkspaceDocumentStatus(cid, request.url, local.status);
      if (local.kind === "error") {
        const status = local.status;
        return new Response(status === 404 ? "Not Found" : "Forbidden", {
          status,
        });
      }
      const headers = new Headers();
      headers.set("Content-Type", local.mime);
      headers.set("Content-Security-Policy", WORKSPACE_CSP);
      headers.set("X-Content-Type-Options", "nosniff");
      headers.set("Cache-Control", "no-store");
      const bytes = local.body.buffer.slice(
        local.body.byteOffset,
        local.body.byteOffset + local.body.byteLength,
      ) as ArrayBuffer;
      return new Response(bytes, { status: 200, headers });
    }

    let upstream: Response;
    try {
      upstream = await bearerFetch(
        workspaceFilePath(resolved.workspaceId, resolved.rel),
      );
    } catch {
      noteWorkspaceDocumentStatus(cid, request.url, 502);
      return new Response("Bad Gateway", { status: 502 });
    }
    if (!upstream.ok) {
      const status = upstream.status === 404 ? 404 : 502;
      noteWorkspaceDocumentStatus(cid, request.url, status);
      return new Response(status === 404 ? "Not Found" : "Upstream Error", {
        status,
      });
    }

    const headers = new Headers();
    headers.set("Content-Type", mimeForPath(resolved.rel));
    headers.set("Content-Security-Policy", WORKSPACE_CSP);
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Cache-Control", "no-store");
    noteWorkspaceDocumentStatus(cid, request.url, 200);
    return new Response(upstream.body, { status: 200, headers });
  });
}

/** 测试接缝：重置注册标记。 */
export function resetWorkspaceProtocolForTests(): void {
  registeredPartitions.clear();
}
