import type {
  OutgoingAgentMention,
  OutgoingAttachment,
} from "@/services/streamConversation";
import { create } from "zustand";

/** 同对话 FIFO 排队项（live · 设备通道快照为权威；进程内无持久化，重连靠账号快照回填）。
 * 连接播种 `turn_queue_account_snapshot` 整表替换云队；增量 `turn_queue_snapshot`。 */
export interface QueuedTurnEntry {
  queueId: string;
  conversationId: string;
  /**
   * 主时间线用户气泡 id（发送 ack 即入场并填写；取消顺带删泡）。
   */
  messageId?: string;
  content: string;
  /**
   * 排队附件（含 ``workspace_path`` 等驻留引用）。
   * 真源是服务端 ``QueuedTurnItem.attachments``；禁止另造路径。
   */
  attachments?: OutgoingAttachment[];
  /** 排队 ``@`` 点名；真源是服务端 ``QueuedTurnItem.agent_mentions``。 */
  agentMentions?: OutgoingAgentMention[];
  position: number;
  queueDepth: number;
  degradedFrom?: "steer";
  /**
   * 非空 = 该项由用户插话升格进队（协调升队 / 经典 steer leftover）。
   * 条上标注「来自你的插话」，仍可按项取消 / 立刻插队。
   */
  interjectionId?: string;
}

export const TURN_QUEUE_SNAPSHOT_TYPE = "turn_queue_snapshot";
export const TURN_QUEUE_ACCOUNT_SNAPSHOT_TYPE = "turn_queue_account_snapshot";

interface QueuedTurnsState {
  byConversation: Record<string, QueuedTurnEntry[]>;
  upsert: (entry: QueuedTurnEntry) => void;
  remove: (conversationId: string, queueId: string) => QueuedTurnEntry | null;
  /** 增量快照权威替换（空数组 = 清这一条会话；禁止整表清空）。 */
  replaceConversation: (
    conversationId: string,
    entries: QueuedTurnEntry[],
  ) => void;
  /**
   * 账号级整表替换云队。`keepKey` 为真的本机 key（sidecar / 本地容器）原样保留。
   * 空表 = 只清云队。
   */
  replaceAll: (
    cloudByConversation: Record<string, QueuedTurnEntry[]>,
    keepKey: (conversationId: string) => boolean,
  ) => void;
  clearConversation: (conversationId: string) => void;
  list: (conversationId: string | null | undefined) => QueuedTurnEntry[];
}

export const useQueuedTurnsStore = create<QueuedTurnsState>((set, get) => ({
  byConversation: {},

  upsert: (entry) =>
    set((state) => {
      const prev = state.byConversation[entry.conversationId] ?? [];
      const without = prev.filter((e) => e.queueId !== entry.queueId);
      return {
        byConversation: {
          ...state.byConversation,
          [entry.conversationId]: [...without, entry].sort(
            (a, b) => a.position - b.position,
          ),
        },
      };
    }),

  remove: (conversationId, queueId) => {
    const prev = get().byConversation[conversationId] ?? [];
    const hit = prev.find((e) => e.queueId === queueId) ?? null;
    if (!hit) return null;
    set((state) => {
      const next = (state.byConversation[conversationId] ?? []).filter(
        (e) => e.queueId !== queueId,
      );
      const byConversation = { ...state.byConversation };
      if (next.length === 0) delete byConversation[conversationId];
      else byConversation[conversationId] = next;
      return { byConversation };
    });
    return hit;
  },

  replaceConversation: (conversationId, entries) =>
    set((state) => {
      const byConversation = { ...state.byConversation };
      if (entries.length === 0) {
        delete byConversation[conversationId];
      } else {
        byConversation[conversationId] = [...entries].sort(
          (a, b) => a.position - b.position,
        );
      }
      return { byConversation };
    }),

  replaceAll: (cloudByConversation, keepKey) =>
    set((state) => {
      const next: Record<string, QueuedTurnEntry[]> = {};
      for (const [id, entries] of Object.entries(state.byConversation)) {
        if (keepKey(id)) next[id] = entries;
      }
      for (const [id, entries] of Object.entries(cloudByConversation)) {
        if (keepKey(id) || entries.length === 0) continue;
        next[id] = [...entries].sort((a, b) => a.position - b.position);
      }
      return { byConversation: next };
    }),

  clearConversation: (conversationId) =>
    set((state) => {
      if (!state.byConversation[conversationId]) return state;
      const byConversation = { ...state.byConversation };
      delete byConversation[conversationId];
      return { byConversation };
    }),

  list: (conversationId) =>
    conversationId ? (get().byConversation[conversationId] ?? []) : [],
}));

export function useQueuedTurns(
  conversationId: string | null | undefined,
): QueuedTurnEntry[] {
  return useQueuedTurnsStore((s) =>
    conversationId ? (s.byConversation[conversationId] ?? EMPTY) : EMPTY,
  );
}

export function conversationHasQueuedTurns(
  conversationId: string | null | undefined,
): boolean {
  return Boolean(
    conversationId &&
      useQueuedTurnsStore.getState().list(conversationId).length > 0,
  );
}

const EMPTY: QueuedTurnEntry[] = [];
