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
   * 已落库的用户行 id。排队期间时间线不画这一行；取消用它删行；出队按它放进时间线。
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
   * 非空 = 该项由经典插话赶不上下一步升格进队。协调插话不再写入；旧行字段照旧。
   * 条上标注「来自你的插话」，仍可按项取消 / 立刻插队。
   */
  interjectionId?: string;
}

export const TURN_QUEUE_SNAPSHOT_TYPE = "turn_queue_snapshot";
export const TURN_QUEUE_ACCOUNT_SNAPSHOT_TYPE = "turn_queue_account_snapshot";

interface QueuedTurnsState {
  byConversation: Record<string, QueuedTurnEntry[]>;
  /**
   * 这通对话的排队快照已经对过账（增量快照 / 本机 hydrate / 账号快照触及）。
   * 未对账前，插话「将在下一条回复处理」不因队里暂时没有而收掉。
   */
  authoritativeIds: Record<string, true>;
  /** ``turn_queued`` 已到、下一次快照还没到。这段窗口不把「队里没有」当成已取消。 */
  staleIds: Record<string, true>;
  /** 账号级云队快照已到过。云对话不在表里 = 队是空的。 */
  cloudQueueReady: boolean;
  upsert: (entry: QueuedTurnEntry) => void;
  remove: (conversationId: string, queueId: string) => QueuedTurnEntry | null;
  /** 本地改顺序。id 列表必须是当前集合，否则不动。 */
  reorder: (conversationId: string, queueIds: string[]) => void;
  /** 改一条的正文 / 附件 / @。不在队里则不动，返回 null。 */
  patchPayload: (
    conversationId: string,
    queueId: string,
    payload: Pick<QueuedTurnEntry, "content" | "attachments" | "agentMentions">,
  ) => QueuedTurnEntry | null;
  markQueueStale: (conversationId: string) => void;
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
  authoritativeIds: {},
  staleIds: {},
  cloudQueueReady: false,

  markQueueStale: (conversationId) => {
    if (!conversationId) return;
    set((state) => {
      if (state.staleIds[conversationId]) return state;
      return { staleIds: { ...state.staleIds, [conversationId]: true } };
    });
  },

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

  reorder: (conversationId, queueIds) =>
    set((state) => {
      const prev = state.byConversation[conversationId] ?? [];
      if (prev.length === 0) return state;
      if (queueIds.length !== prev.length) return state;
      const byId = new Map(prev.map((entry) => [entry.queueId, entry]));
      if (queueIds.some((id) => !byId.has(id))) return state;
      const next = queueIds.map((id, index) => {
        const entry = byId.get(id);
        if (!entry) return null;
        return {
          ...entry,
          position: index + 1,
          queueDepth: queueIds.length,
        };
      });
      if (next.some((entry) => entry == null)) return state;
      return {
        byConversation: {
          ...state.byConversation,
          [conversationId]: next as QueuedTurnEntry[],
        },
      };
    }),

  patchPayload: (conversationId, queueId, payload) => {
    const prev = get().byConversation[conversationId] ?? [];
    const hit = prev.find((entry) => entry.queueId === queueId) ?? null;
    if (!hit) return null;
    set((state) => ({
      byConversation: {
        ...state.byConversation,
        [conversationId]: (state.byConversation[conversationId] ?? []).map(
          (entry) =>
            entry.queueId === queueId ? { ...entry, ...payload } : entry,
        ),
      },
    }));
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
      const staleIds = { ...state.staleIds };
      delete staleIds[conversationId];
      return {
        byConversation,
        staleIds,
        authoritativeIds: {
          ...state.authoritativeIds,
          [conversationId]: true,
        },
      };
    }),

  replaceAll: (cloudByConversation, keepKey) =>
    set((state) => {
      const next: Record<string, QueuedTurnEntry[]> = {};
      const authoritativeIds = { ...state.authoritativeIds };
      const staleIds = { ...state.staleIds };
      for (const id of Object.keys(state.byConversation)) {
        if (keepKey(id)) continue;
        authoritativeIds[id] = true;
        delete staleIds[id];
      }
      for (const [id, entries] of Object.entries(state.byConversation)) {
        if (keepKey(id)) next[id] = entries;
      }
      for (const [id, entries] of Object.entries(cloudByConversation)) {
        if (keepKey(id)) continue;
        authoritativeIds[id] = true;
        delete staleIds[id];
        if (entries.length === 0) continue;
        next[id] = [...entries].sort((a, b) => a.position - b.position);
      }
      return {
        byConversation: next,
        authoritativeIds,
        staleIds,
        cloudQueueReady: true,
      };
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

/**
 * 插话仍标着 queued，但排队快照已经对过账、队里没有这条。
 * 等待徽章不画。``turn_queued`` 到了、快照还没到的窗口不算已取消。
 * 本机队只认这通对话自己的快照，不拿云账号空表当权威。
 */
export function useInterjectionQueueWithdrawn(
  conversationId: string | null | undefined,
  interjectionId: string | null | undefined,
  localQueue: boolean,
): boolean {
  return useQueuedTurnsStore((s) => {
    if (!conversationId || !interjectionId) return false;
    if (s.staleIds[conversationId]) return false;
    const authoritative =
      s.authoritativeIds[conversationId] === true ||
      (!localQueue && s.cloudQueueReady);
    if (!authoritative) return false;
    const items = s.byConversation[conversationId] ?? EMPTY;
    return !items.some((e) => e.interjectionId === interjectionId);
  });
}
