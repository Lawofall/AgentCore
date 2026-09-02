/**
 * Hydrate attach/settle is independent of message-window adopt.
 *
 * Warm reopen (slice already has messages) must still enter settle/attach;
 * cold empty-slice adopt path must keep calling the same branches.
 */
import type { ConversationRecovery } from "@/services/resume";
import { useAiAttentionStore } from "@/stores/aiAttention";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  attachOnOpen,
  settleCloudRunningAssistant,
  settleOrphanEmptyAssistants,
  attachSidecarTurn,
  projectUnsyncedTurns,
  projectPausedRuns,
  syncConversationFollow,
} = vi.hoisted(() => ({
  attachOnOpen: vi.fn(async () => {}),
  settleCloudRunningAssistant: vi.fn(async () => "ghost" as const),
  settleOrphanEmptyAssistants: vi.fn(),
  attachSidecarTurn: vi.fn(async () => true),
  projectUnsyncedTurns: vi.fn(),
  projectPausedRuns: vi.fn(),
  syncConversationFollow: vi.fn(),
}));

vi.mock("../turns/recovery", () => ({
  attachOnOpen,
  settleCloudRunningAssistant,
  settleOrphanEmptyAssistants,
}));

vi.mock("../turns/conversationFollow", () => ({
  syncConversationFollow,
}));

vi.mock("../turns/sidecarAttach", () => ({
  attachSidecarTurn,
}));

vi.mock("../turns/projectUnsynced", () => ({
  projectUnsyncedTurns,
}));

vi.mock("../turns/projectPausedRuns", () => ({
  projectPausedRuns,
}));

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

import {
  awaitHydrateAttachSettle,
  runHydrateAttachSettle,
  scheduleHydrateAttachSettle,
} from "../turns/hydrateAttachSettle";
import {
  beginLocalConversationStream,
  hasLocalConversationStream,
  resetStreamOwnershipForTests,
} from "../turns/streamOwnership";

const CID = "conv-hydrate-attach";

function seedMessages(
  last:
    | { role: "user" }
    | { role: "assistant"; status: "running" | "complete"; id?: string },
): void {
  const store = useConversationStore.getState();
  store.switchConversation(CID);
  store.addMessage(
    {
      id: "u1",
      role: "user",
      content: "q",
      createdAt: "2026-01-01T00:00:00Z",
      executionId: null,
      isStreaming: false,
    },
    CID,
  );
  if (last.role === "user") return;
  store.addMessage(
    {
      id: last.id ?? "a1",
      role: "assistant",
      content: last.status === "running" ? "partial" : "done",
      createdAt: "2026-01-01T00:00:01Z",
      executionId: null,
      isStreaming: last.status === "running",
      status: last.status,
      serverMessageId: last.id ?? "a1",
    },
    CID,
  );
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useAiAttentionStore.setState({ entries: [] });
  resetStreamOwnershipForTests();
  attachOnOpen.mockClear();
  settleCloudRunningAssistant.mockClear();
  settleOrphanEmptyAssistants.mockClear();
  attachSidecarTurn.mockClear();
  attachSidecarTurn.mockImplementation(async () => true);
  projectUnsyncedTurns.mockClear();
  projectPausedRuns.mockClear();
  syncConversationFollow.mockClear();
  vi.stubGlobal("window", { __WEB__: true });
});

describe("runHydrateAttachSettle (warm reopen / cold adopt)", () => {
  it("warm reopen with running assistant still settles (messages.length>0)", async () => {
    seedMessages({ role: "assistant", status: "running" });
    expect(getRuntime(CID).messages.length).toBeGreaterThan(0);

    const branch = await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(branch).toBe("cloud");
    expect(settleCloudRunningAssistant).toHaveBeenCalledTimes(1);
    expect(settleCloudRunningAssistant).toHaveBeenCalledWith(
      CID,
      expect.objectContaining({
        cloudLive: false,
        cloudKnown: true,
        pausedCount: 0,
      }),
    );
    expect(attachOnOpen).not.toHaveBeenCalled();
    expect(attachSidecarTurn).not.toHaveBeenCalled();
  });

  it("warm reopen with last user + cloudLive attaches", async () => {
    seedMessages({ role: "user" });
    expect(getRuntime(CID).messages.at(-1)?.role).toBe("user");

    const branch = await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: true,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(branch).toBe("cloud");
    expect(attachOnOpen).toHaveBeenCalledTimes(1);
    expect(attachOnOpen).toHaveBeenCalledWith(CID);
    expect(settleCloudRunningAssistant).not.toHaveBeenCalled();
  });

  it("cold-style local sidecarLive still attaches (adopt-success path parity)", async () => {
    // Empty slice mirrors post-adopt readiness; branch must still attach.
    useConversationStore.getState().switchConversation(CID);
    expect(getRuntime(CID).messages.length).toBe(0);

    const branch = await runHydrateAttachSettle(CID, {
      sidecarLive: true,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(branch).toBe("local");
    expect(projectUnsyncedTurns).toHaveBeenCalledTimes(1);
    expect(settleOrphanEmptyAssistants).toHaveBeenCalledWith(CID);
    expect(attachSidecarTurn).toHaveBeenCalledTimes(1);
    // Hydrate 不传页级 signal（切会话 ≠ 卸观察泵）。
    expect(attachSidecarTurn).toHaveBeenCalledWith(CID);
    expect(projectPausedRuns).not.toHaveBeenCalled();
    expect(attachOnOpen).not.toHaveBeenCalled();
    expect(settleCloudRunningAssistant).not.toHaveBeenCalled();
  });

  it("local paused skips attach but projects pause-frame runs", async () => {
    seedMessages({ role: "assistant", status: "complete", id: "a-paused" });
    const pausedRuns = {
      "a-paused": {
        events: [
          {
            type: "run_plan",
            payload: { execution_id: "exec-1" },
            timestamp: "t0",
          },
        ],
        finish_reason: "paused",
      },
    };

    const branch = await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 1,
      unsynced: [],
      pausedRuns,
    });

    expect(branch).toBe("local");
    expect(attachSidecarTurn).not.toHaveBeenCalled();
    expect(projectUnsyncedTurns).toHaveBeenCalledTimes(1);
    expect(settleOrphanEmptyAssistants).toHaveBeenCalledWith(CID);
    expect(projectPausedRuns).toHaveBeenCalledTimes(1);
    expect(projectPausedRuns).toHaveBeenCalledWith(CID, pausedRuns);
  });

  it("cloud complete assistant settles orphans but does not attach/ghost", async () => {
    seedMessages({ role: "assistant", status: "complete" });

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: true,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(attachOnOpen).not.toHaveBeenCalled();
    expect(settleCloudRunningAssistant).not.toHaveBeenCalled();
    expect(settleOrphanEmptyAssistants).toHaveBeenCalledWith(CID);
  });

  it("prefers runtime tail over a stale window (warm memory newer)", async () => {
    // Memory already has running assistant; a stale fetched window would have
    // ended on user — settle must follow runtime, not the window.
    seedMessages({ role: "assistant", status: "running" });
    expect(getRuntime(CID).messages.at(-1)?.role).toBe("assistant");

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(settleCloudRunningAssistant).toHaveBeenCalledTimes(1);
    expect(attachOnOpen).not.toHaveBeenCalled();
  });

  it("云会话不在 hydrate 里订 follow（揭窗才订）；本机引擎在跑不卸订", async () => {
    seedMessages({ role: "assistant", status: "complete" });

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });
    expect(syncConversationFollow).not.toHaveBeenCalled();

    syncConversationFollow.mockClear();
    await runHydrateAttachSettle(CID, {
      sidecarLive: true,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });
    expect(syncConversationFollow).not.toHaveBeenCalled();
  });

  it("sidecarLive 不拆 follow；attach 前已占本端闸，返回后释放", async () => {
    seedMessages({ role: "assistant", status: "running" });
    attachSidecarTurn.mockImplementation(async () => {
      expect(hasLocalConversationStream(CID)).toBe(true);
      return true;
    });

    const branch = await runHydrateAttachSettle(CID, {
      sidecarLive: true,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(branch).toBe("local");
    expect(syncConversationFollow).not.toHaveBeenCalled();
    expect(attachSidecarTurn).toHaveBeenCalledTimes(1);
    expect(hasLocalConversationStream(CID)).toBe(false);
  });

  it("纯云冷挂起也不在 hydrate 里订：揭窗即订，hydrate 只卸 unsynced", async () => {
    seedMessages({ role: "assistant", status: "complete", id: "a-paused" });

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 1,
      unsynced: [],
    });

    expect(syncConversationFollow).not.toHaveBeenCalled();
  });

  it("未同步本机回合卸订标 unsynced，不冒充 switched_away", async () => {
    seedMessages({ role: "assistant", status: "complete" });

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [{} as ConversationRecovery["unsynced"][number]],
    });

    expect(syncConversationFollow).toHaveBeenCalledWith(null, "unsynced");
  });

  it("迟到的 hydrate 不抢订阅：用户已切走就不动全局那一条", async () => {
    seedMessages({ role: "assistant", status: "complete" });
    useConversationStore.getState().switchConversation("conv-other");

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(syncConversationFollow).not.toHaveBeenCalled();
  });

  it("skips settle/attach when local stream already pumping", async () => {
    seedMessages({ role: "assistant", status: "running" });
    const release = beginLocalConversationStream(CID);

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: true,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(settleCloudRunningAssistant).not.toHaveBeenCalled();
    expect(attachOnOpen).not.toHaveBeenCalled();
    expect(attachSidecarTurn).not.toHaveBeenCalled();
    expect(projectPausedRuns).not.toHaveBeenCalled();
    release();
  });

  it("cold overlay isGenerating without abort still settles", async () => {
    // Mirrors adoptMessageWindow → shouldSetGeneratingOnHydrate: spinner on,
    // abort still null until attach/settle claims it.
    seedMessages({ role: "assistant", status: "running" });
    useConversationStore.getState().setGenerating(true, CID);
    expect(getRuntime(CID).abort).toBeNull();

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(settleCloudRunningAssistant).toHaveBeenCalledTimes(1);
  });

  it("hydrate overlay generating + sidecarLive still attaches", async () => {
    seedMessages({ role: "assistant", status: "running" });
    useConversationStore.getState().setGenerating(true, CID);
    expect(getRuntime(CID).abort).toBeNull();
    expect(hasLocalConversationStream(CID)).toBe(false);

    const branch = await runHydrateAttachSettle(CID, {
      sidecarLive: true,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(branch).toBe("local");
    expect(attachSidecarTurn).toHaveBeenCalledTimes(1);
    expect(attachSidecarTurn).toHaveBeenCalledWith(CID);
  });

  it("打开对话不清 ai_attention 灯", async () => {
    useAiAttentionStore.setState({
      entries: [
        {
          interactionId: "appr-1",
          conversationId: CID,
          turnId: "t1",
          kind: "approval",
          title: "需要授权：终端",
        },
      ],
    });
    seedMessages({ role: "assistant", status: "complete" });

    await runHydrateAttachSettle(CID, {
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });

    expect(useAiAttentionStore.getState().entries).toHaveLength(1);
    expect(useAiAttentionStore.getState().entries[0].conversationId).toBe(CID);
  });

  it("awaitHydrateAttachSettle returns before a live sidecar attach finishes", async () => {
    seedMessages({ role: "assistant", status: "running" });
    let finishAttach!: (ok: boolean) => void;
    attachSidecarTurn.mockImplementation(
      () =>
        new Promise<boolean>((resolve) => {
          finishAttach = resolve;
        }),
    );

    const settled = awaitHydrateAttachSettle(
      CID,
      Promise.resolve({
        sidecarLive: true,
        cloudLive: false,
        cloudKnown: true,
        pausedCount: 0,
        unsynced: [],
      }),
    );

    await expect(settled).resolves.toBe("local");
    expect(attachSidecarTurn).toHaveBeenCalledTimes(1);
    expect(projectUnsyncedTurns).toHaveBeenCalledWith(CID, []);
    expect(hasLocalConversationStream(CID)).toBe(true);
    finishAttach(true);
    await vi.waitFor(() => {
      expect(hasLocalConversationStream(CID)).toBe(false);
    });
  });

  it("awaitHydrateAttachSettle runs settle after recovery resolves", async () => {
    seedMessages({ role: "assistant", status: "complete" });
    let resolveRecovery!: (value: ConversationRecovery) => void;
    const recoveryLoaded = new Promise<ConversationRecovery>((resolve) => {
      resolveRecovery = resolve;
    });

    const pending = awaitHydrateAttachSettle(CID, recoveryLoaded);
    expect(settleOrphanEmptyAssistants).not.toHaveBeenCalled();

    resolveRecovery({
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });
    await pending;
    expect(settleOrphanEmptyAssistants).toHaveBeenCalledWith(CID);
  });

  it("scheduleHydrateAttachSettle returns before recovery lands", async () => {
    seedMessages({ role: "assistant", status: "complete" });
    let resolveRecovery!: (value: ConversationRecovery) => void;
    const recoveryLoaded = new Promise<ConversationRecovery>((resolve) => {
      resolveRecovery = resolve;
    });

    scheduleHydrateAttachSettle(CID, recoveryLoaded);
    expect(settleOrphanEmptyAssistants).not.toHaveBeenCalled();

    resolveRecovery({
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });
    await vi.waitFor(() => {
      expect(settleOrphanEmptyAssistants).toHaveBeenCalledWith(CID);
    });
  });
});
