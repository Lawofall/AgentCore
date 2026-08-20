import type { components } from "@/types/api.generated";

/**
 * Shared display helpers for 记忆已更新 rows (conversation-tail card + `/memory`
 * 「最近更新」feed). Quota cards reuse the same action chips as a change log so
 * 「什么没写进来 / 谁占着配额」reads like any other memory row; the `quota`
 * fingerprint row is backend bookkeeping and must never reach the user.
 */

type MemoryUpdateAction =
  components["schemas"]["MemoryUpdateItemView"]["action"];

export const MEMORY_UPDATE_ACTION_META: Record<
  MemoryUpdateAction,
  { label: string; cls: string }
> = {
  add: { label: "新增", cls: "mem-add" },
  update: { label: "更新", cls: "mem-update-on" },
  remove: { label: "移除", cls: "mem-remove" },
  quota: { label: "配额", cls: "mem-update-other" },
  quota_denied: { label: "未写入", cls: "mem-update-other" },
  quota_holder: { label: "占用", cls: "mem-update-other" },
};

/** Drop the backend-only fingerprint row so 「N 项」matches what the card lists. */
export function visibleMemoryUpdateItems<T extends { action: string }>(
  items: readonly T[],
): T[] {
  return items.filter((it) => it.action !== "quota");
}
