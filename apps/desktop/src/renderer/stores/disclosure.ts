import { registerConversationUiClearer, uiGet, uiSet } from "@/lib/uiStorage";
import { useConversationStore } from "@/stores/conversation";
import { useCallback, useEffect, useRef, useState } from "react";
import { create } from "zustand";

/**
 * 对话页「折叠/展开」偏好的持久化（AI 页交互状态持久化）—— 统一原语，取代散落各组件的
 * 一次性 `useState`（那类是**会话内存态**，一离开该组件即丢：切对话、刷新/重启、
 * 重开运行详情面板都会把用户手动展开/收起的选择清回默认）。
 *
 * 与仓内既有、已验证的 `components/files/fileTreeExpanded.ts`（按树 id 存展开集）
 * 对齐：**只存「偏离默认」的项**——一旦某条被切回其默认值即删键——所以这张 map
 * 收敛在用户真正动过的少数折叠上，不随历史回合无限增长。删除对话时经
 * {@link clearConversationUiState} / {@link clearDisclosureForConversation} 连带清理。
 *
 * **键的作用域**：有效键 = `${当前对话 id}::${调用方给的稳定 key}`。对话 id 由 hook 内部读，故调用方
 * 只需给「回合/运行 + 元素」这段稳定标识（messageId / runId / step.id / 轮号 / sideKey / 通道+序号…）。
 * 无对话 id（草稿/流式裸态）或未给 key 时**退化为会话内存态**（普通 `useState`），保证任何环境都不炸。
 *
 * 纯前端 UX 层、经 {@link uiGet}/{@link uiSet} 落盘，**不碰协议 fold / conformance**。
 */

const STORAGE_KEY = "disclosure";
const SCOPE_SEP = "::";

/** 读回持久化的「偏离默认」map。损坏 / 不可用时回退空表（退化为会话内存态）。 */
function load(): Record<string, boolean> {
  const parsed = uiGet<Record<string, unknown>>(STORAGE_KEY);
  if (!parsed || typeof parsed !== "object") return {};
  const out: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(parsed)) {
    if (typeof v === "boolean") out[k] = v;
  }
  return out;
}

function persist(map: Record<string, boolean>): void {
  if (Object.keys(map).length === 0) uiSet(STORAGE_KEY, undefined);
  else uiSet(STORAGE_KEY, map);
}

interface DisclosureState {
  /** 只含「偏离默认」的项：value === 该项默认值时即删键，故表恒收敛。 */
  map: Record<string, boolean>;
  /** 写入一个折叠状态；`def` 为该项默认值——等于默认即删键（收敛不膨胀），否则记下偏离值。 */
  setKey: (fullKey: string, value: boolean, def: boolean) => void;
  /** 删除某对话名下的全部折叠偏好（对话被删时连带清理，守「不无限增长」）。 */
  clearConversation: (conversationId: string) => void;
}

export const useDisclosureStore = create<DisclosureState>((set) => ({
  map: load(),
  setKey: (fullKey, value, def) =>
    set((s) => {
      const cur = s.map[fullKey];
      // 无变化则不触发订阅者重渲染（默认态 & 未记录、或值未变）。
      if (value === def ? cur === undefined : cur === value) return s;
      const map = { ...s.map };
      if (value === def) delete map[fullKey];
      else map[fullKey] = value;
      persist(map);
      return { map };
    }),
  clearConversation: (conversationId) =>
    set((s) => {
      const prefix = `${conversationId}${SCOPE_SEP}`;
      const keys = Object.keys(s.map).filter((k) => k.startsWith(prefix));
      if (keys.length === 0) return s;
      const map = { ...s.map };
      for (const k of keys) delete map[k];
      persist(map);
      return { map };
    }),
}));

registerConversationUiClearer((conversationId) => {
  useDisclosureStore.getState().clearConversation(conversationId);
});

/** 命令式清理入口（对话删除时亦可走 {@link clearConversationUiState}）。 */
export function clearDisclosureForConversation(conversationId: string): void {
  useDisclosureStore.getState().clearConversation(conversationId);
}

/**
 * 持久化的折叠开关——`useState<boolean>` 的替身，但用户的选择会跨卸载/重挂/刷新存活。
 *
 * @param key 稳定的「回合/运行 + 元素」标识（对话 id 由内部补上作用域）。传 `null` → 退化为会话态。
 * @param defaultOpen 默认展开态。**只有偏离它的选择才落盘**（切回默认即删键，表收敛不膨胀）。
 * @returns `[expanded, setExpanded]`，签名与 `useState<boolean>` 一致（支持函数式更新）。
 */
export function usePersistentDisclosure(
  key: string | null,
  defaultOpen: boolean,
): readonly [boolean, (next: boolean | ((prev: boolean) => boolean)) => void] {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const setKey = useDisclosureStore((s) => s.setKey);
  const fullKey =
    key && conversationId ? `${conversationId}${SCOPE_SEP}${key}` : null;
  const stored = useDisclosureStore((s) =>
    fullKey ? s.map[fullKey] : undefined,
  );
  // 无对话 id / 无 key 时的会话内存态兜底（草稿、流式裸态、测试环境）。恒调用以稳定 hook 顺序。
  const [session, setSession] = useState(defaultOpen);

  const value = fullKey ? (stored ?? defaultOpen) : session;

  const setValue = useCallback(
    (next: boolean | ((prev: boolean) => boolean)) => {
      if (!fullKey) {
        setSession((prev) =>
          typeof next === "function"
            ? (next as (p: boolean) => boolean)(prev)
            : next,
        );
        return;
      }
      // 从最新落盘态取 prev，避免闭包读到旧值 + 让本回调 identity 稳定（不随 value 变）。
      const prev = useDisclosureStore.getState().map[fullKey] ?? defaultOpen;
      const resolved =
        typeof next === "function"
          ? (next as (p: boolean) => boolean)(prev)
          : next;
      setKey(fullKey, resolved, defaultOpen);
    },
    [fullKey, defaultOpen, setKey],
  );

  return [value, setValue] as const;
}

/**
 * 「直播中自动、收场后尊重保存值」的折叠开关（AI 页交互状态持久化 · Q3 决策）——给那些**流式期间
 * 想让你盯着看、收场后又该按你意愿记住**的披露块（思考过程 / 工具组 / 运行详情工具段 / 质询环节…）。
 *
 * - `live === true`（回合仍在流）：默认展开（`liveDefault`）盯直播，**不写盘**；你仍可临时收起/展开
 *   （`liveOverride`，仅本次直播有效）。
 * - `live` 由真转假（收场）：清掉直播临时态，**回到持久化的用户选择**（`settledDefault`，默认收起）。
 *   于是既保留「边流边看、看完自动收」的原体验，又根治「流式中手动展开 → 收场被强制收起且忘掉」。
 *
 * @returns `[expanded, toggle, setExpanded]`。
 */
export function useStreamAwareDisclosure(
  key: string | null,
  live: boolean,
  opts?: { liveDefault?: boolean; settledDefault?: boolean },
): readonly [boolean, () => void, (next: boolean) => void] {
  const liveDefault = opts?.liveDefault ?? true;
  const settledDefault = opts?.settledDefault ?? false;
  const [stored, setStored] = usePersistentDisclosure(key, settledDefault);
  const [liveOverride, setLiveOverride] = useState<boolean | null>(null);
  const prevLive = useRef(live);

  useEffect(() => {
    // 收场（true→false）：丢弃直播临时态，交回持久化选择接管。
    if (prevLive.current && !live) setLiveOverride(null);
    prevLive.current = live;
  }, [live]);

  const expanded = live ? (liveOverride ?? liveDefault) : stored;

  const toggle = useCallback(() => {
    if (live) setLiveOverride((v) => !(v ?? liveDefault));
    else setStored((v) => !v);
  }, [live, liveDefault, setStored]);

  const setExpanded = useCallback(
    (next: boolean) => {
      if (live) setLiveOverride(next);
      else setStored(next);
    },
    [live, setStored],
  );

  return [expanded, toggle, setExpanded] as const;
}
