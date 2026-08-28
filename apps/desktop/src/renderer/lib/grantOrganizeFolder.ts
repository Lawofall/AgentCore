import { adoptServerAlias } from "@/lib/adoptServerAlias";
import { hasLocalFiles } from "@/lib/capabilities";
import type { GrantFolderHints } from "@/lib/grantFolderHints";
import { revokeExternalGrant } from "@/lib/revokeExternalGrant";
import { ApiError, api } from "@/services/api";
import { invalidateExternalGrants } from "@/services/externalGrants";
import type {
  FsRoot,
  GrantSessionReadonlyRootFailReason,
} from "@shared/ipc-contract";

export type { GrantFolderHints } from "@/lib/grantFolderHints";

export type GrantOrganizeFailReason =
  | "unavailable"
  | GrantSessionReadonlyRootFailReason
  | "error";

export type GrantOrganizeResult =
  | {
      ok: true;
      root: FsRoot;
      alias: string;
      namespace: string;
      displayLabel?: string;
    }
  | { ok: false; reason: "unavailable" }
  | {
      ok: false;
      reason: Exclude<GrantOrganizeFailReason, "unavailable">;
      message: string;
    };

const ALIAS_STORE_FAILED = "授权已登记但本机没能记下挂载名，请重试";

const RESOLVE_FAIL_FALLBACK: Record<
  Exclude<GrantSessionReadonlyRootFailReason, "invalid">,
  string
> = {
  not_found: "找不到该目录",
  permission_denied: "定位到了，但这台电脑不让程序读取该目录",
  not_directory: "路径指向的是文件，不是目录",
  ambiguous: "匹配到多个目录，请说得更具体",
};

type ExternalGrantBody = { grant: { alias: string; namespace: string } };

function describeGrantError(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) {
    return "对话不存在或无权访问";
  }
  return e instanceof Error ? e.message : "授权失败，请重试";
}

/** Answer text so the CEO sees which folder was granted (no absolute path). */
export function formatGrantOrganizeFolderAnswer(
  optionLabel: string,
  folderName: string,
  namespace: string,
): string {
  return `${optionLabel}（${folderName} → ${namespace}；可移动/重命名/复制/删除进回收站、仅本次对话、可撤销）`;
}

export function formatGrantAttachFolderAnswer(
  optionLabel: string,
  folderName: string,
  namespace: string,
): string {
  return `${optionLabel}（${folderName} → ${namespace}；本对话可改可覆盖、仅本次对话、可撤销）`;
}

/**
 * Resolve grant hints → session organize root → POST grant.
 * Never opens a folder picker; unresolved → not_found (≠ cancelled).
 * Same root upgrading from readonly still requires this fresh confirm card.
 */
export async function pickAndGrantSessionFolder(
  conversationId: string,
  mode: "organize" | "attach_rw",
  hints?: GrantFolderHints,
): Promise<GrantOrganizeResult> {
  if (!hasLocalFiles() || !window.fsApi?.grantSessionReadonlyRoot) {
    return { ok: false, reason: "unavailable" };
  }
  try {
    const granted = await window.fsApi.grantSessionReadonlyRoot({
      conversationId,
      mode,
      ...(hints?.path ? { path: hints.path } : {}),
      ...(hints?.wellKnown ? { wellKnown: hints.wellKnown } : {}),
      ...(hints?.targetName ? { targetName: hints.targetName } : {}),
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
    // 与只读授权同理，这一趟也把根绑到本设备的履约会话（服务端 `fulfill/declare.py`）。
    try {
      body = await api.post<ExternalGrantBody>(
        `/v1/conversations/${conversationId}/workspace/external-grants`,
        {
          root_id: root.id,
          label: root.name,
          mode,
        },
      );
    } catch (e) {
      await revokeExternalGrant(conversationId, root.id);
      throw e;
    }
    // 别名只有这一个来源。存不下就没有可用的挂载（本机引擎按它解析路径），当作授权失败。
    if (!(await adoptServerAlias(conversationId, root.id, body.grant.alias))) {
      await revokeExternalGrant(conversationId, root.id);
      return { ok: false, reason: "error", message: ALIAS_STORE_FAILED };
    }
    invalidateExternalGrants(conversationId);
    return {
      ok: true,
      root,
      alias: body.grant.alias,
      namespace: body.grant.namespace,
      displayLabel: granted.displayLabel,
    };
  } catch (e) {
    return { ok: false, reason: "error", message: describeGrantError(e) };
  }
}

export async function pickAndGrantOrganizeFolder(
  conversationId: string,
  hints?: GrantFolderHints,
): Promise<GrantOrganizeResult> {
  return pickAndGrantSessionFolder(conversationId, "organize", hints);
}

export async function pickAndGrantAttachFolder(
  conversationId: string,
  hints?: GrantFolderHints,
): Promise<GrantOrganizeResult> {
  return pickAndGrantSessionFolder(conversationId, "attach_rw", hints);
}
