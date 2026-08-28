import { isWebPreview } from "@/lib/preview";
import { fetchConversationAudit } from "@/services/audit";
import { useEffect } from "react";
import { create } from "zustand";

/**
 * 会话级「权限切换」store —— 聊天主流里那条系统提示行的数据源。
 *
 * 数据面复用既有会话级审计 REST（`GET …/audit?category=permission`）。
 * 切换经 `PUT …/permission-axes` 在返回前**同步**写下一条 `permission.axes_changed`
 * 审计行，故切换成功后由 {@link PermissionChangeState.load} 重拉即可。
 */

export type PermissionChange = {
  /** 审计行 id（时间线 key）。 */
  id: string;
  /** 切换发生时刻（ISO，用于时间线锚定）。 */
  at: string;
  /** 切换前的原始 payload（axes 对象或旧档位字符串）。 */
  previous: unknown;
  /** 切换后的原始 payload。 */
  next: unknown;
};

/** 会话审计响应 → 权限切换列表（axes_changed；兼容旧 preset_changed）。 */
function toChanges(
  rows: Awaited<ReturnType<typeof fetchConversationAudit>>,
): PermissionChange[] {
  return (rows?.data ?? [])
    .filter(
      (e) =>
        e.action === "permission.axes_changed" ||
        e.action === "permission.preset_changed",
    )
    .map((e) => ({
      id: e.id,
      at: e.created_at,
      previous: e.detail?.previous ?? "",
      next: e.detail?.permission_axes ?? e.detail?.permission_preset ?? "",
    }));
}

interface PermissionChangeState {
  byConversation: Record<string, PermissionChange[]>;
  load: (conversationId: string) => Promise<void>;
}

export const usePermissionChangeStore = create<PermissionChangeState>(
  (set) => ({
    byConversation: {},
    load: async (conversationId) => {
      const rows = await fetchConversationAudit(conversationId, {
        category: "permission",
      });
      set((s) => ({
        byConversation: {
          ...s.byConversation,
          [conversationId]: toChanges(rows),
        },
      }));
    },
  }),
);

const EMPTY: PermissionChange[] = [];

export function usePermissionChanges(
  conversationId: string | null,
): PermissionChange[] {
  return usePermissionChangeStore((s) =>
    conversationId ? (s.byConversation[conversationId] ?? EMPTY) : EMPTY,
  );
}

/**
 * 打开会话时拉一次权限切换记录。会话内新切换由 PermissionAxesBadge /
 * ApprovalPrompt「全放行」命令式重拉。
 */
export function usePermissionChangesSync(conversationId: string | null): void {
  const load = usePermissionChangeStore((s) => s.load);
  useEffect(() => {
    if (conversationId && !isWebPreview()) {
      void load(conversationId).catch(() => {});
    }
  }, [conversationId, load]);
}
