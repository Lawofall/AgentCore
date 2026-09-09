/**
 * 「哪个对话在等你」——账号级信号 `ai_attention` / `ai_attention_snapshot`。
 *
 * 回合停在阻塞卡上时后端发 `required`，任一端放行（或超时 / 孤儿 / Stop）后发
 * `resolved`。只带对话与 kind 短句、不带卡的正文——正文永远由该对话自己的流 / REST
 * 重取（设计 §2.2「只送信号不送内容」）。
 *
 * 权威是 fulfill 播种：`ai_attention_snapshot` 整表 replace（空表也 replace，用来
 * 灭断线期间的假灯）。增量 `ai_attention` 走 fulfill，过渡期也可从 realtime 入站；
 * replace 只认 fulfill 快照。打开对话不再清灯——当前页的 banner / 提醒自己过滤。
 *
 * 本存储只回答：**哪些对话正停着等人**。侧栏「等你」灯与跨对话提醒都读它。对话页
 * 内真正的可操作面仍是 ApprovalPrompt / ResumePrompt。
 */
import { useMemo } from "react";
import { create } from "zustand";

export const AI_ATTENTION_SNAPSHOT_TYPE = "ai_attention_snapshot";
export const AI_ATTENTION_TYPE = "ai_attention";

/** realtime 扁平帧，或 fulfill 增量 payload（字段相同，fulfill 多包一层 payload）。 */
export interface AiAttentionEvent {
  type?: "ai_attention";
  state: "required" | "resolved";
  conversation_id: string;
  turn_id: string;
  interaction_id: string;
  kind: string;
  title: string;
}

export interface AiAttentionEntry {
  /** 与 InteractionStore 条目 id / pausedTurns.checkpointId 同一个 id（跨通道去重靠它）。 */
  interactionId: string;
  conversationId: string;
  turnId: string;
  kind: string;
  /** kind 短句（服务端 `attention_title`），不是卡上的问题。 */
  title: string;
}

interface AiAttentionState {
  /** 按到达先后排列——提醒不跳序。快照 replace 用服务端给的顺序。 */
  entries: AiAttentionEntry[];
  apply: (event: AiAttentionEvent) => void;
  replace: (entries: AiAttentionEntry[]) => void;
  clearConversation: (conversationId: string) => void;
  clear: () => void;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function entryFromFields(raw: {
  interaction_id?: unknown;
  conversation_id?: unknown;
  turn_id?: unknown;
  kind?: unknown;
  title?: unknown;
}): AiAttentionEntry | null {
  const interactionId = text(raw.interaction_id);
  const conversationId = text(raw.conversation_id);
  if (!interactionId || !conversationId) return null;
  return {
    interactionId,
    conversationId,
    turnId: text(raw.turn_id),
    kind: text(raw.kind),
    title: text(raw.title),
  };
}

function sameEntries(
  a: readonly AiAttentionEntry[],
  b: readonly AiAttentionEntry[],
): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.interactionId !== y.interactionId ||
      x.conversationId !== y.conversationId ||
      x.turnId !== y.turnId ||
      x.kind !== y.kind ||
      x.title !== y.title
    ) {
      return false;
    }
  }
  return true;
}

export const useAiAttentionStore = create<AiAttentionState>((set) => ({
  entries: [],

  apply: (event) => {
    const entry = entryFromFields(event);
    if (!entry) return;

    if (event.state === "resolved") {
      set((state) => {
        const next = state.entries.filter(
          (e) => e.interactionId !== entry.interactionId,
        );
        return next.length === state.entries.length ? state : { entries: next };
      });
      return;
    }
    if (event.state !== "required") return;

    set((state) => {
      const index = state.entries.findIndex(
        (e) => e.interactionId === entry.interactionId,
      );
      if (index < 0) return { entries: [...state.entries, entry] };
      const next = state.entries.slice();
      next[index] = entry;
      return { entries: next };
    });
  },

  replace: (entries) =>
    set((state) => (sameEntries(state.entries, entries) ? state : { entries })),

  clearConversation: (conversationId) =>
    set((state) => {
      if (!conversationId) return state;
      const next = state.entries.filter(
        (e) => e.conversationId !== conversationId,
      );
      return next.length === state.entries.length ? state : { entries: next };
    }),

  clear: () =>
    set((state) => (state.entries.length === 0 ? state : { entries: [] })),
}));

/** 应用一帧增量 `ai_attention`（fulfill payload 或 realtime 扁平事件）。 */
export function applyAiAttention(event: AiAttentionEvent): void {
  useAiAttentionStore.getState().apply(event);
}

/**
 * fulfill 播种：整份 `{ entries }` replace。缺字段 / 非数组的帧丢掉，不清现有表。
 * 空数组 = 灭断线假灯。
 */
export function applyAiAttentionSnapshot(payload: unknown): void {
  if (!payload || typeof payload !== "object") return;
  const raw = (payload as { entries?: unknown }).entries;
  if (!Array.isArray(raw)) return;
  const entries: AiAttentionEntry[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const entry = entryFromFields(item as Record<string, unknown>);
    if (!entry || seen.has(entry.interactionId)) continue;
    seen.add(entry.interactionId);
    entries.push(entry);
  }
  useAiAttentionStore.getState().replace(entries);
}

/** 仍导出：登出 / 测试用。打开对话不再走这条。 */
export function clearAiAttentionForConversation(conversationId: string): void {
  useAiAttentionStore.getState().clearConversation(conversationId);
}

/** 清空——提醒是会话内的东西，登出 / 关流即作废。 */
export function clearAiAttention(): void {
  useAiAttentionStore.getState().clear();
}

/** 该对话是否正停着等人（跨对话信号；与页内卡的判定并联点亮侧栏灯）。 */
export function useConversationAwaitingAttention(
  conversationId: string,
): boolean {
  return useAiAttentionStore((s) =>
    s.entries.some((e) => e.conversationId === conversationId),
  );
}

/** 当前 required 对话 id 集合（侧栏回塞 / 折组覆盖）。 */
export function useRequiredConversationIds(): ReadonlySet<string> {
  const entries = useAiAttentionStore((s) => s.entries);
  return useMemo(() => {
    const ids = new Set<string>();
    for (const e of entries) ids.add(e.conversationId);
    return ids;
  }, [entries]);
}

/** 快照读（非 React 调用方：跨对话提醒的去重与对账）。 */
export function aiAttentionEntries(): readonly AiAttentionEntry[] {
  return useAiAttentionStore.getState().entries;
}

/** Banner / 提醒：可按当前页过滤，不必清 store。 */
export function aiAttentionEntriesExcept(
  conversationId: string | null,
): readonly AiAttentionEntry[] {
  const entries = useAiAttentionStore.getState().entries;
  if (!conversationId) return entries;
  return entries.filter((e) => e.conversationId !== conversationId);
}
