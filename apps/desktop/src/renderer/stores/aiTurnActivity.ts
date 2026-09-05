/**
 * 「哪些云对话还在跑」——账号级 fulfill 信号 `ai_turn_activity_snapshot` /
 * `ai_turn_activity`（设备长连接 `GET /v1/fulfill`）。
 *
 * 对话级 SSE 同时只留一条，所以另一条对话在跑时本端没有任何显示流可走。设备通道按账号
 * 开、每台在线桌面一条，连接时播种整份 running 集合（客户端 replace），之后按对话增量
 * 进出。帧带的是事实，不回头 GET；断线靠下一帧 snapshot 整表替换，禁止「打开即清」。
 *
 * 本机 sidecar / 本地容器对话不吃这路云信号——它们的生成态仍以本端 `isGenerating`
 * 与本机协作图活体为准，否则同一条对话会被云 running 与本机流各点一次灯。
 */
import {
  type ConversationRuntime,
  assistantProjectionId,
  runtimeOf,
  useConversationStore,
} from "@/stores/conversation";
import {
  execRuntime,
  isConversationExecutionLive,
  useExecutionStore,
} from "@/stores/execution";
import { create } from "zustand";

export const AI_TURN_ACTIVITY_SNAPSHOT_TYPE = "ai_turn_activity_snapshot";
export const AI_TURN_ACTIVITY_TYPE = "ai_turn_activity";

export type AiTurnActivityDoneReason =
  | "completed"
  | "paused"
  | "stopped"
  | "error";

export type AiTurnActivityLastDone = {
  conversationId: string;
  reason: AiTurnActivityDoneReason;
  seq: number;
};

interface AiTurnActivityState {
  running: ReadonlySet<string>;
  lastDone: AiTurnActivityLastDone | null;
  replaceRunning: (ids: string[]) => void;
  applyActivity: (payload: unknown) => void;
  clear: () => void;
}

function asIdList(raw: unknown[]): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (typeof item !== "string" || !item || seen.has(item)) continue;
    seen.add(item);
    ids.push(item);
  }
  return ids;
}

function sameIds(running: ReadonlySet<string>, ids: string[]): boolean {
  if (running.size !== ids.length) return false;
  for (const id of ids) {
    if (!running.has(id)) return false;
  }
  return true;
}

function isDoneReason(value: unknown): value is AiTurnActivityDoneReason {
  return (
    value === "completed" ||
    value === "paused" ||
    value === "stopped" ||
    value === "error"
  );
}

export const useAiTurnActivityStore = create<AiTurnActivityState>((set) => ({
  running: new Set(),
  lastDone: null,

  replaceRunning: (ids) =>
    set((state) => {
      if (sameIds(state.running, ids)) return state;
      return { running: new Set(ids) };
    }),

  applyActivity: (payload) => {
    if (!payload || typeof payload !== "object") return;
    const p = payload as {
      conversation_id?: unknown;
      state?: unknown;
      reason?: unknown;
    };
    const conversationId =
      typeof p.conversation_id === "string" ? p.conversation_id : "";
    if (!conversationId) return;

    if (p.state === "running") {
      set((state) => {
        if (state.running.has(conversationId)) return state;
        const running = new Set(state.running);
        running.add(conversationId);
        return { running };
      });
      return;
    }

    if (p.state !== "done") return;

    set((state) => {
      let running = state.running;
      if (running.has(conversationId)) {
        const next = new Set(running);
        next.delete(conversationId);
        running = next;
      }
      if (!isDoneReason(p.reason)) {
        return running === state.running ? state : { running };
      }
      return {
        running,
        lastDone: {
          conversationId,
          reason: p.reason,
          seq: (state.lastDone?.seq ?? 0) + 1,
        },
      };
    });
  },

  clear: () =>
    set((state) =>
      state.running.size === 0 && state.lastDone === null
        ? state
        : { running: new Set(), lastDone: null },
    ),
}));

/** 连接播种：整份 `{ running }` replace。缺字段 / 非数组的帧丢掉，不清现有集合。 */
export function applyAiTurnActivitySnapshot(payload: unknown): void {
  if (!payload || typeof payload !== "object") return;
  const running = (payload as { running?: unknown }).running;
  if (!Array.isArray(running)) return;
  useAiTurnActivityStore.getState().replaceRunning(asIdList(running));
}

/** 增量：`{ conversation_id, state, reason }`。running 无 reason；done 才认 reason。 */
export function applyAiTurnActivity(payload: unknown): void {
  useAiTurnActivityStore.getState().applyActivity(payload);
}

export function clearAiTurnActivity(): void {
  useAiTurnActivityStore.getState().clear();
}

export function useConversationCloudRunning(conversationId: string): boolean {
  return useAiTurnActivityStore((s) => s.running.has(conversationId));
}

/**
 * 本机引擎对话不吃云 running：`executionVia=sidecar` 或本地容器对话。
 * 云过桥（`cloud_bridge`）仍认云信号。
 */
export function ignoresCloudTurnActivity(
  executionVia: ConversationRuntime["executionVia"],
  localContainerRootId?: string | null,
): boolean {
  return executionVia === "sidecar" || localContainerRootId != null;
}

/**
 * 本对话已加载切片上，是否还有一张协作图在转（含主管收口后队员托管）。
 * 从未打开过的对话没有切片，这里为 false——跨会话只靠云 running / isGenerating。
 */
export function useConversationGraphLive(conversationId: string): boolean {
  const projectionKey = useConversationStore((s) => {
    let key = "";
    for (const m of runtimeOf(s, conversationId).messages) {
      if (m.role === "assistant") key += `${assistantProjectionId(m)}\0`;
    }
    return key;
  });
  return useExecutionStore((s) => {
    if (!projectionKey) return false;
    for (const mid of projectionKey.split("\0")) {
      if (mid && isConversationExecutionLive(execRuntime(s, mid))) return true;
    }
    return false;
  });
}

/** 侧栏状态点：等你灯 > 协作图活体 / 本端 isGenerating / 云 running。 */
export function conversationSidebarActivityStatus(input: {
  awaiting: boolean;
  cloudRunning: boolean;
  isGenerating: boolean;
  executionVia: ConversationRuntime["executionVia"];
  localContainerRootId?: string | null;
  /** 本机已加载切片上的协作图仍在转。 */
  graphLive?: boolean;
}): "running" | "awaiting" | null {
  if (input.awaiting) return "awaiting";
  if (input.graphLive || input.isGenerating) return "running";
  const ignoreCloud = ignoresCloudTurnActivity(
    input.executionVia,
    input.localContainerRootId,
  );
  if (!ignoreCloud && input.cloudRunning) return "running";
  return null;
}
