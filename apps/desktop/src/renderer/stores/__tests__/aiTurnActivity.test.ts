import {
  applyAiTurnActivity,
  applyAiTurnActivitySnapshot,
  conversationSidebarActivityStatus,
  ignoresCloudTurnActivity,
  useAiTurnActivityStore,
} from "@/stores/aiTurnActivity";
import { afterEach, describe, expect, it } from "vitest";

const A = "conv-a";
const B = "conv-b";

function runningIds(): string[] {
  return [...useAiTurnActivityStore.getState().running];
}

afterEach(() => {
  useAiTurnActivityStore.getState().clear();
});

describe("ai_turn_activity_snapshot", () => {
  it("replace 整份 running 集合", () => {
    applyAiTurnActivitySnapshot({ running: [A] });
    applyAiTurnActivitySnapshot({ running: [B, A] });
    expect(runningIds()).toEqual([B, A]);

    applyAiTurnActivitySnapshot({ running: [] });
    expect(runningIds()).toEqual([]);
  });

  it("缺 running / 非数组的帧丢掉，不清现有集合", () => {
    applyAiTurnActivitySnapshot({ running: [A] });
    applyAiTurnActivitySnapshot(null);
    applyAiTurnActivitySnapshot({});
    applyAiTurnActivitySnapshot({ running: "nope" });
    expect(runningIds()).toEqual([A]);
  });

  it("snapshot 不写 lastDone（重连替换不弹完成）", () => {
    applyAiTurnActivity({ conversation_id: A, state: "running" });
    applyAiTurnActivitySnapshot({ running: [] });
    expect(useAiTurnActivityStore.getState().lastDone).toBeNull();
    expect(runningIds()).toEqual([]);
  });
});

describe("ai_turn_activity", () => {
  it("running 进集合，done 带 reason 出集合并记 lastDone", () => {
    applyAiTurnActivity({ conversation_id: A, state: "running" });
    expect(runningIds()).toEqual([A]);
    expect(useAiTurnActivityStore.getState().lastDone).toBeNull();

    applyAiTurnActivity({
      conversation_id: A,
      state: "done",
      reason: "completed",
    });
    expect(runningIds()).toEqual([]);
    expect(useAiTurnActivityStore.getState().lastDone).toMatchObject({
      conversationId: A,
      reason: "completed",
      seq: 1,
    });
  });

  it("done 无合法 reason 仍移出 running，但不记 lastDone", () => {
    applyAiTurnActivity({ conversation_id: A, state: "running" });
    applyAiTurnActivity({ conversation_id: A, state: "done" });
    expect(runningIds()).toEqual([]);
    expect(useAiTurnActivityStore.getState().lastDone).toBeNull();
  });

  it("缺 conversation_id / 未知 state 丢掉", () => {
    applyAiTurnActivity({ conversation_id: A, state: "running" });
    applyAiTurnActivity({ state: "running" });
    applyAiTurnActivity({ conversation_id: B, state: "queued" });
    expect(runningIds()).toEqual([A]);
  });
});

describe("conversationSidebarActivityStatus", () => {
  it("等你灯压过云 running 与本端 isGenerating", () => {
    expect(
      conversationSidebarActivityStatus({
        awaiting: true,
        cloudRunning: true,
        isGenerating: true,
        executionVia: null,
        localContainerRootId: null,
      }),
    ).toBe("awaiting");
  });

  it("云 running 在没打开过的对话上也能亮执行中", () => {
    expect(
      conversationSidebarActivityStatus({
        awaiting: false,
        cloudRunning: true,
        isGenerating: false,
        executionVia: null,
        localContainerRootId: null,
      }),
    ).toBe("running");
  });

  it("sidecar 忽略云 running，只认本端 isGenerating（不双计）", () => {
    expect(
      conversationSidebarActivityStatus({
        awaiting: false,
        cloudRunning: true,
        isGenerating: false,
        executionVia: "sidecar",
        localContainerRootId: null,
      }),
    ).toBeNull();
    expect(
      conversationSidebarActivityStatus({
        awaiting: false,
        cloudRunning: true,
        isGenerating: true,
        executionVia: "sidecar",
        localContainerRootId: null,
      }),
    ).toBe("running");
  });

  it("本地容器对话忽略云 running", () => {
    expect(
      conversationSidebarActivityStatus({
        awaiting: false,
        cloudRunning: true,
        isGenerating: false,
        executionVia: null,
        localContainerRootId: "root-1",
      }),
    ).toBeNull();
    expect(ignoresCloudTurnActivity(null, "root-1")).toBe(true);
    expect(ignoresCloudTurnActivity("cloud_bridge", null)).toBe(false);
  });

  it("sidecar 上协作图仍在转时亮执行中（不靠 isGenerating / 云 running）", () => {
    expect(
      conversationSidebarActivityStatus({
        awaiting: false,
        cloudRunning: false,
        isGenerating: false,
        executionVia: "sidecar",
        localContainerRootId: null,
        graphLive: true,
      }),
    ).toBe("running");
  });

  it("等你灯压过协作图活体", () => {
    expect(
      conversationSidebarActivityStatus({
        awaiting: true,
        cloudRunning: false,
        isGenerating: false,
        executionVia: "sidecar",
        localContainerRootId: null,
        graphLive: true,
      }),
    ).toBe("awaiting");
  });
});
