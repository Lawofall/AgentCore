/**
 * 云桌标准出口编排（§五 · §7.6）：ZIP / 导出到本机文件夹（工作区工具条）/
 * 合回落点登记与 Diff 勾选合回（工作区芯片）/ ① 仅产物快捷合回（产物卡）。
 *
 * 合回主路径 = 云快照 zip（内存）vs 落点现态 → handoff-review 判定 → MergeLandingReview。
 * 不经 applyHandoffJob；≠ mode=local、≠ 过桥默认。整树 checkout 仅「导出到本机文件夹」旁路。
 * ① 仅产物 = delivery 投影路径逐文件写落点（见 mergeArtifactsOnly）；≠ 整树覆盖。
 *
 * 已知限制（首刀）：无 last-merge base（同路径异内容一律 conflict）；不做云删→落点删；
 * 单文件 >5MB / 整包 >100MB / 文件数过多诚实跳过或拒绝。
 */

import { getConversations } from "@/hooks/useConversations";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  type MergeLandingScope,
  clearMergeLanding,
  getMergeLanding,
  resolveMergeLandingScope,
  setMergeLanding,
} from "@/lib/mergeLandingPreference";
import {
  notifyActionError,
  notifyInfo,
  notifySuccess,
  notifyWarning,
} from "@/lib/toast";
import {
  type MergeArtifactRef,
  resolveMergeArtifactRefs,
  writeArtifactsToLanding,
} from "@/services/mergeArtifactsOnly";
import { prepareMergeLandingDiff } from "@/services/mergeLandingDiff";
import {
  exportWorkspaceToLocal,
  exportWorkspaceZip,
} from "@/services/workspace";
import { getRuntime, lastAssistantProjectionId } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { useMergeLandingReviewStore } from "@/stores/mergeLandingReview";
import type { FsRoot } from "@shared/ipc-contract";

export type CloudDeskExitResult =
  | { ok: true }
  | {
      ok: false;
      reason: "cancelled" | "unavailable" | "error";
      message?: string;
    };

function scopeForConversation(conversationId: string): MergeLandingScope {
  const conv = getConversations().find((c) => c.id === conversationId);
  return resolveMergeLandingScope(conversationId, conv?.folderId);
}

function rootName(roots: FsRoot[], rootId: string): string | null {
  return roots.find((r) => r.id === rootId)?.name ?? null;
}

/** 当前会话/项目已登记的合回落点（根可能已失效，调用方应用 roots 校验）。 */
export function peekMergeLanding(
  conversationId: string,
  roots: FsRoot[],
): { rootId: string; rootName: string | null; missing: boolean } | null {
  const entry = getMergeLanding(scopeForConversation(conversationId));
  if (!entry) return null;
  const name = rootName(roots, entry.rootId);
  return {
    rootId: entry.rootId,
    rootName: name,
    missing: !roots.some((r) => r.id === entry.rootId),
  };
}

/**
 * 选本机文件夹并登记为合回落点（addRoot 授权；≠ 创建本地工作区）。
 */
export async function registerMergeLanding(
  conversationId: string,
): Promise<CloudDeskExitResult & { root?: FsRoot }> {
  if (!hasLocalFiles() || !window.fsApi?.addRoot) {
    return {
      ok: false,
      reason: "unavailable",
      message: "当前环境无法登记合回落点",
    };
  }
  const picked = await window.fsApi.addRoot();
  if (!picked.ok) {
    if (picked.reason === "cancelled")
      return { ok: false, reason: "cancelled" };
    return {
      ok: false,
      reason: "error",
      message: picked.message || "未能登记合回落点",
    };
  }
  setMergeLanding(scopeForConversation(conversationId), picked.root.id);
  return { ok: true, root: picked.root };
}

export async function exportCloudDeskZip(
  conversationId: string,
): Promise<CloudDeskExitResult> {
  try {
    await exportWorkspaceZip(conversationId);
    notifySuccess("已导出 ZIP");
    return { ok: true };
  } catch (e) {
    notifyActionError("导出 ZIP 失败", e);
    return {
      ok: false,
      reason: "error",
      message: e instanceof Error ? e.message : "导出 ZIP 失败",
    };
  }
}

/** 每次弹目录；不必先登记合回落点。 */
export async function exportCloudDeskToPickedFolder(
  conversationId: string,
): Promise<CloudDeskExitResult> {
  try {
    const result = await exportWorkspaceToLocal(conversationId);
    if (result.ok) {
      notifySuccess(
        `已导出 ${result.fileCount} 个文件到「${result.destName}」`,
      );
      return { ok: true };
    }
    if (result.reason === "cancelled")
      return { ok: false, reason: "cancelled" };
    if (result.reason === "unavailable") {
      await exportWorkspaceZip(conversationId);
      notifySuccess("已导出 ZIP");
      return { ok: true };
    }
    notifyActionError("导出到本机失败", new Error(result.message));
    return { ok: false, reason: "error", message: result.message };
  } catch (e) {
    notifyActionError("导出到本机失败", e);
    return {
      ok: false,
      reason: "error",
      message: e instanceof Error ? e.message : "导出失败",
    };
  }
}

/** 解析/登记合回落点（缺则 picker）；与 Diff 合回共用偏好。 */
async function ensureMergeLanding(
  conversationId: string,
  roots: FsRoot[],
): Promise<
  | { ok: true; rootId: string; rootName: string }
  | Extract<CloudDeskExitResult, { ok: false }>
> {
  const landing = peekMergeLanding(conversationId, roots);
  if (!landing || landing.missing) {
    if (landing?.missing) {
      clearMergeLanding(scopeForConversation(conversationId));
    }
    const registered = await registerMergeLanding(conversationId);
    if (!registered.ok) return registered;
    const root = registered.root;
    if (!root) {
      return {
        ok: false,
        reason: "error",
        message: "未能登记合回落点",
      };
    }
    return {
      ok: true,
      rootId: root.id,
      rootName: root.name,
    };
  }
  return {
    ok: true,
    rootId: landing.rootId,
    rootName: landing.rootName ?? "所选目录",
  };
}

/** 本回合（最近一条助手）delivery_status 投影。 */
export function latestTurnDeliveryStatus(conversationId: string) {
  const key = lastAssistantProjectionId(getRuntime(conversationId).messages);
  if (!key) return null;
  return useExecutionStore.getState().byId[key]?.deliveryStatus ?? null;
}

/**
 * 合回到本机：Diff 勾选写入已登记落点（冲突默认留本地）。
 */
export async function mergeBackToLanding(
  conversationId: string,
  roots: FsRoot[],
): Promise<CloudDeskExitResult> {
  if (!hasLocalFiles() || !window.fsApi?.workspaceOp) {
    return {
      ok: false,
      reason: "unavailable",
      message: "当前环境无法合回到本机",
    };
  }

  const landing = await ensureMergeLanding(conversationId, roots);
  if (!landing.ok) return landing;

  const label = landing.rootName;
  try {
    const prepared = await prepareMergeLandingDiff(
      conversationId,
      landing.rootId,
      label,
    );
    const outcome = await useMergeLandingReviewStore
      .getState()
      .openSession(prepared);
    if (outcome.applied) {
      notifySuccess(`合回「${label}」：${outcome.summaryLabel}`);
      return { ok: true };
    }
    if (outcome.reason === "busy") {
      notifyInfo("已有合回评审进行中");
      return {
        ok: false,
        reason: "unavailable",
        message: "已有合回评审进行中",
      };
    }
    return { ok: false, reason: "cancelled" };
  } catch (e) {
    notifyActionError("合回到本机失败", e);
    return {
      ok: false,
      reason: "error",
      message: e instanceof Error ? e.message : "合回失败",
    };
  }
}

/**
 * §7.6 ① 只合回产物：仅写交付路径到落点，不碰其余本机树。
 * `refsOverride` 有值时用调用方清单（产物卡=该回合），否则读最近一条助手 delivery。
 */
export async function mergeArtifactsOnlyToLanding(
  conversationId: string,
  roots: FsRoot[],
  refsOverride?: MergeArtifactRef[],
): Promise<CloudDeskExitResult> {
  if (!hasLocalFiles() || !window.fsApi?.workspaceOp) {
    return {
      ok: false,
      reason: "unavailable",
      message: "当前环境无法合回产物",
    };
  }

  const refs =
    refsOverride !== undefined
      ? refsOverride
      : resolveMergeArtifactRefs(latestTurnDeliveryStatus(conversationId));
  if (refs.length === 0) {
    notifyInfo("本回合无交付产物");
    return {
      ok: false,
      reason: "unavailable",
      message: "本回合无交付产物",
    };
  }

  const landing = await ensureMergeLanding(conversationId, roots);
  if (!landing.ok) return landing;

  const label = landing.rootName;
  try {
    const summary = await writeArtifactsToLanding({
      conversationId,
      rootId: landing.rootId,
      refs,
    });

    if (summary.errors.length > 0 && summary.written.length === 0) {
      const first = summary.errors[0];
      const detail = first?.detail || "写入失败";
      notifyActionError("只合回产物失败", new Error(detail));
      return { ok: false, reason: "error", message: detail };
    }

    const parts: string[] = [];
    if (summary.written.length > 0) {
      parts.push(`已写入 ${summary.written.length} 个`);
    }
    if (summary.skippedExisting.length > 0) {
      parts.push(`跳过已有 ${summary.skippedExisting.length} 个`);
      notifyWarning("落点已有同路径文件，已跳过（未覆盖）", {
        description: summary.skippedExisting.slice(0, 5).join("、"),
      });
    }
    if (summary.errors.length > 0) {
      parts.push(`失败 ${summary.errors.length} 个`);
    }

    notifySuccess(`只合回产物「${label}」：${parts.join(" · ") || "无变更"}`);
    return { ok: true };
  } catch (e) {
    notifyActionError("只合回产物失败", e);
    return {
      ok: false,
      reason: "error",
      message: e instanceof Error ? e.message : "合回失败",
    };
  }
}
