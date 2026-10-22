import { adoptServerAlias } from "@/lib/adoptServerAlias";
import { hasLocalFiles } from "@/lib/capabilities";
import { type GrantFolderHints, grantIpcFields } from "@/lib/grantFolderHints";
import { revokeExternalGrant } from "@/lib/revokeExternalGrant";
import { ApiError, api } from "@/services/api";
import { invalidateExternalGrants } from "@/services/externalGrants";
import { peekConversationBoundary } from "@/services/permissionAxes";
import type {
  FsRoot,
  GrantSessionReadonlyRootFailReason,
} from "@shared/ipc-contract";

export type { GrantFolderHints } from "@/lib/grantFolderHints";

export type GrantReadonlyFailReason =
  | "unavailable"
  | GrantSessionReadonlyRootFailReason
  | "error";

export type GrantReadonlyResult =
  | {
      ok: true;
      root: FsRoot;
      alias: string;
      namespace: string;
      workingRoot?: boolean;
      displayLabel?: string;
    }
  | { ok: false; reason: "unavailable" }
  | {
      ok: false;
      reason: Exclude<GrantReadonlyFailReason, "unavailable">;
      message: string;
    };

const ALIAS_STORE_FAILED = "授权已登记但本机没能记下别名，请重试";

const RESOLVE_FAIL_FALLBACK: Record<
  Exclude<GrantSessionReadonlyRootFailReason, "invalid">,
  string
> = {
  not_found: "找不到该目录",
  permission_denied: "定位到了，但这台电脑不让程序读取该目录",
  not_directory: "路径指向的是文件，不是目录",
  ambiguous: "匹配到多个目录，请说得更具体",
  cancelled: "用户拒绝授权",
};

type ExternalGrantBody = {
  grant: { alias: string; namespace: string; working_root?: boolean };
};

function describeGrantError(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) {
    return "对话不存在或无权访问";
  }
  return e instanceof Error ? e.message : "授权失败，请重试";
}

/** Answer text so the CEO sees which folder was granted (no absolute path). */
export function formatGrantReadonlyFolderAnswer(
  optionLabel: string,
  folderName: string,
  workingRoot = false,
): string {
  const role = workingRoot ? "本次对话正在用" : "额外文件夹";
  return `${optionLabel}（${folderName}；${role}，可撤销）`;
}

/**
 * Resolve grant hints (path / wellKnown / targetName) → session read-only root →
 * POST grant to server. Never opens a folder picker; unresolved → not_found
 * (≠ cancelled). Orthogonal to workspace binding.
 */
export async function pickAndGrantReadonlyFolder(
  conversationId: string,
  hints?: GrantFolderHints,
): Promise<GrantReadonlyResult> {
  if (!hasLocalFiles() || !window.fsApi?.grantSessionReadonlyRoot) {
    return { ok: false, reason: "unavailable" };
  }
  try {
    const boundary = await peekConversationBoundary(conversationId);
    const granted = await window.fsApi.grantSessionReadonlyRoot({
      conversationId,
      mode: "readonly",
      ...grantIpcFields(hints),
      ...(boundary ? { boundary } : {}),
    });
    if (!granted.ok) {
      const reason = granted.reason;
      if (reason === "invalid") {
        return {
          ok: false,
          reason: "error",
          message: granted.message ?? "无效的请求参数",
        };
      }
      return {
        ok: false,
        reason,
        message: granted.message ?? RESOLVE_FAIL_FALLBACK[reason],
      };
    }
    const root = granted.root;
    let body: ExternalGrantBody;
    // 只兜 POST：登记不上就撤回本机授权，别让这台机器留着服务端不知道的根。
    // 这一趟同时把根绑到本设备的履约会话上（服务端 `fulfill/declare.py`），所以
    // 回执一到，同一轮里紧接着下发的 `external/<别名>/` 操作就有机器可路由。
    try {
      body = await api.post<ExternalGrantBody>(
        `/v1/conversations/${conversationId}/workspace/external-grants`,
        {
          root_id: root.id,
          label: root.name,
        },
      );
    } catch (e) {
      await revokeExternalGrant(conversationId, root.id);
      throw e;
    }
    // 别名只有这一个来源。存不下就没有可用的挂载（本机引擎按它解析路径），当作授权失败。
    if (
      !(await adoptServerAlias(
        conversationId,
        root.id,
        body.grant.alias,
        body.grant.working_root,
      ))
    ) {
      await revokeExternalGrant(conversationId, root.id);
      return { ok: false, reason: "error", message: ALIAS_STORE_FAILED };
    }
    invalidateExternalGrants(conversationId);
    return {
      ok: true,
      root,
      alias: body.grant.alias,
      namespace: body.grant.namespace,
      workingRoot: body.grant.working_root === true,
      displayLabel: granted.displayLabel,
    };
  } catch (e) {
    return { ok: false, reason: "error", message: describeGrantError(e) };
  }
}
