import { create } from "zustand";

/**
 * 发送门闩（跨组件重挂载）。
 *
 * 门闩曾经是 `useComposerSend` 里的组件实例级 `useRef`，组件重挂载或换一个 composer
 * 实例（切对话回来、居中草稿 → 底栏、刷新 / 重启）就归零——线上 7 天 8 起「一次发送建出两条内容
 * 相同的会话、各自跑完整轮双倍计费」正由此而来。相位与草稿同键
 * （{@link import("@/stores/composer").draftKeyFor}），存在 store 里，任何一个
 * composer 皮肤重新挂上来都看得见在途的那次发送。
 *
 * 门闩只是第一道闸：真正兜住「以为没发出去又按一次」的是创建请求上的幂等键
 * （见 {@link import("@/lib/draftRequestId").resolveDraftRequestId}）。
 */
export type ComposerSendPhase =
  /** 草稿正在换成真会话（创建 POST 在途）：输入框已清空，界面必须有明确的进行中态。 */
  | "creating"
  /** 回合正在上路（附件收尾 → 发回合）。 */
  | "sending";

interface ComposerSendState {
  /** draftKey → 相位；无键 = 空闲。 */
  phase: Record<string, ComposerSendPhase>;
}

export const useComposerSendStore = create<ComposerSendState>(() => ({
  phase: {},
}));

/** 取门闩；已被占用返回 false —— 调用方必须直接放弃这次发送。 */
export function acquireComposerSendLatch(
  key: string,
  phase: ComposerSendPhase,
): boolean {
  const current = useComposerSendStore.getState().phase;
  if (current[key]) return false;
  useComposerSendStore.setState({ phase: { ...current, [key]: phase } });
  return true;
}

/**
 * 草稿 promote 成真会话后把门闩迁到新键：draftKey 翻转的那一帧不能漏出可点的发送键。
 * 一次 setState 完成，中途不存在「两边都没闩上」的状态。
 */
export function moveComposerSendLatch(
  from: string,
  to: string,
  phase: ComposerSendPhase,
): void {
  const next = { ...useComposerSendStore.getState().phase };
  delete next[from];
  next[to] = phase;
  useComposerSendStore.setState({ phase: next });
}

/** 放门闩（可重复调用）。 */
export function releaseComposerSendLatch(key: string): void {
  const current = useComposerSendStore.getState().phase;
  if (!current[key]) return;
  const next = { ...current };
  delete next[key];
  useComposerSendStore.setState({ phase: next });
}

/** 该草稿键上在途发送的相位（null = 空闲）。 */
export function useComposerSendPhase(key: string): ComposerSendPhase | null {
  return useComposerSendStore((s) => s.phase[key] ?? null);
}

/** @internal vitest —— 清掉跨用例残留的门闩。 */
export function __resetComposerSendLatchesForTests(): void {
  useComposerSendStore.setState({ phase: {} });
}
