/**
 * Cold-start hydrate regression (D7 二次修订).
 *
 * First acceptance failed because recovery branched on `resolveSidecarRoot`
 * (React Query conversation-list cache — empty after refresh). These tests
 * deliberately do NOT prefill conversation/workspace query caches and do NOT
 * mock resolveSidecarRoot: local recovery must fire from main-process facts alone.
 */
import { useInteractionStore } from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import type { SidecarUnsyncedTurnSummary } from "@shared/sidecar-contract";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();

vi.mock("@/services/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
}));

vi.mock("@/services/sidecarRouting", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/sidecarRouting")>();
  return {
    ...actual,
    // If hydrate/loadRecovery still calls this for branch selection, fail loud.
    resolveSidecarRoot: vi.fn(async () => {
      throw new Error(
        "resolveSidecarRoot must not gate recovery (cold-start lesson)",
      );
    }),
  };
});

import { loadRecovery, shouldHydrateLocalRecovery } from "@/services/resume";

const CID = "conv-cold-start";
const OTHER = "conv-elsewhere";

/** A live-surfaced (SSE pause) shell — the frame /recovery may or may not have seen yet. */
function liveResume(messageId: string, checkpointId: string): PendingResume {
  return {
    messageId,
    conversationId: CID,
    checkpointId,
    kind: "ask_user",
    userMessage: "q",
    userMessageId: "u1",
    steps: [],
    pending: [],
    question: "继续？",
    assumptions: [],
    questions: [],
    intent: "decision",
    origin: "server",
  };
}

function unsyncedSummary(
  over: Partial<SidecarUnsyncedTurnSummary> = {},
): SidecarUnsyncedTurnSummary {
  return {
    user_message_id: "u1",
    user_message: "q",
    message_id: "a1",
    trace_id: "a".repeat(32),
    phase: "ready",
    updated_at: 1,
    content: "ans",
    reasoning_content: null,
    citations: [],
    runs: null,
    finish_reason: "stop",
    input_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    cache_hit_tokens: 0,
    cache_miss_tokens: 0,
    ...over,
  };
}

beforeEach(() => {
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
  useQueuedTurnsStore.setState({ byConversation: {} });
  apiGet.mockReset();
  vi.unstubAllGlobals();
});

describe("loadRecovery cold start (no React Query / no resolveSidecarRoot)", () => {
  it("reports sidecarLive from recovery IPC with empty conversation cache", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: true,
      turnId: "turn-1",
      unsynced: [],
      paused: [],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    const r = await loadRecovery(CID);
    expect(recoveryIpc).toHaveBeenCalledWith({ conversationId: CID });
    expect(r.sidecarLive).toBe(true);
    expect(r.cloudLive).toBe(false);
    expect(r.cloudKnown).toBe(true);
    expect(r.turnId).toBe("turn-1");
    expect(shouldHydrateLocalRecovery(r)).toBe(true);
  });

  it("hydrate writes sidecar queuedTurns via replaceConversation", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: true,
      turnId: "turn-1",
      unsynced: [],
      paused: [],
      queuedTurns: [{ queueId: "q-local", content: "排队句", position: 1 }],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-stale",
      conversationId: CID,
      content: "旧条",
      position: 1,
      queueDepth: 1,
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    await loadRecovery(CID);
    const list = useQueuedTurnsStore.getState().list(CID);
    expect(list.map((e) => e.queueId)).toEqual(["q-local"]);
    expect(list[0]?.content).toBe("排队句");
  });

  it("omitted queuedTurns does not wipe kept local queue", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [],
      paused: [],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-keep",
      conversationId: CID,
      content: "本机队",
      position: 1,
      queueDepth: 1,
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    await loadRecovery(CID);
    expect(
      useQueuedTurnsStore
        .getState()
        .list(CID)
        .map((e) => e.queueId),
    ).toEqual(["q-keep"]);
  });

  it("takes local hydrate path for unsynced-only (no live turn)", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [unsyncedSummary()],
      paused: [],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    const r = await loadRecovery(CID);
    expect(r.sidecarLive).toBe(false);
    expect(r.unsynced).toHaveLength(1);
    expect(shouldHydrateLocalRecovery(r)).toBe(true);
  });

  it("merges paused frames and survives cloud failure", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [],
      paused: [
        {
          message_id: "m-pause",
          kind: "plan_review",
          checkpoint_id: "cp1",
          user_message: "q",
          steps: [],
          pending: [],
        },
      ],
    }));
    apiGet.mockRejectedValue(new Error("network down"));

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    const r = await loadRecovery(CID);
    expect(r.pausedCount).toBe(1);
    expect(r.cloudLive).toBe(false);
    expect(r.cloudKnown).toBe(false);
    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
    expect(usePausedTurnStore.getState().pending[0]?.origin).toBe("sidecar");
  });

  it("tags each mixed-frame with its own origin (not conversation-wide)", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [],
      paused: [
        {
          message_id: "m-local",
          kind: "ask_user",
          checkpoint_id: "cp-local",
          user_message: "local q",
          steps: [],
          pending: [],
        },
      ],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [
        {
          message_id: "m-cloud",
          kind: "plan_review",
          checkpoint_id: "cp-cloud",
          user_message: "cloud q",
          steps: [],
          pending: [],
        },
      ],
      pending_interactions: [],
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    const r = await loadRecovery(CID);
    expect(r.pausedCount).toBe(2);
    const byId = Object.fromEntries(
      usePausedTurnStore.getState().pending.map((p) => [p.messageId, p.origin]),
    );
    expect(byId["m-local"]).toBe("sidecar");
    expect(byId["m-cloud"]).toBe("server");
  });

  it("sidecar wins collision and keeps origin=sidecar", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [],
      paused: [
        {
          message_id: "m-same",
          kind: "ask_user",
          checkpoint_id: "cp-local",
          user_message: "from sidecar",
          steps: [],
          pending: [],
        },
      ],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [
        {
          message_id: "m-same",
          kind: "ask_user",
          checkpoint_id: "cp-cloud",
          user_message: "from cloud",
          steps: [],
          pending: [],
        },
      ],
      pending_interactions: [],
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    await loadRecovery(CID);
    const entries = usePausedTurnStore.getState().pending;
    expect(entries).toHaveLength(1);
    expect(entries[0]?.origin).toBe("sidecar");
    expect(entries[0]?.userMessage).toBe("from sidecar");
  });

  it("web path stays cloud-only (hasLocalEngine false)", async () => {
    apiGet.mockResolvedValue({
      live_running: true,
      paused: [],
      pending_interactions: [],
    });

    vi.stubGlobal("window", {
      __WEB__: true,
      sidecarApi: {
        recovery: vi.fn(async () => {
          throw new Error("must not call local recovery on web");
        }),
      },
    });

    const r = await loadRecovery(CID);
    expect(r.sidecarLive).toBe(false);
    expect(r.cloudLive).toBe(true);
    expect(r.cloudKnown).toBe(true);
    expect(shouldHydrateLocalRecovery(r)).toBe(false);
  });

  it("recovery snapshot carrying ceo_review hydrates it onto the resume frame", async () => {
    // REST schema 未列该字段；宽松读——后端带了就透传，absent → undefined。
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [
        {
          message_id: "m-cr",
          kind: "plan_review",
          checkpoint_id: "cp-cr",
          user_message: "q",
          steps: [{ run_id: "r1", role: "调研", summary: "ok" }],
          pending: [],
          ceo_review: {
            conclusion: "可放行",
            risks: ["预算偏乐观"],
            suggestions: [],
          },
        },
        {
          message_id: "m-no-cr",
          kind: "plan_review",
          checkpoint_id: "cp-no-cr",
          user_message: "q2",
          steps: [],
          pending: [],
        },
      ],
      pending_interactions: [],
    });

    vi.stubGlobal("window", { __WEB__: true });

    await loadRecovery(CID);
    const entries = usePausedTurnStore.getState().pending;
    expect(entries).toHaveLength(2);
    const withReview = entries.find((e) => e.messageId === "m-cr");
    const without = entries.find((e) => e.messageId === "m-no-cr");
    expect(withReview?.ceoReview).toEqual({
      conclusion: "可放行",
      risks: ["预算偏乐观"],
      suggestions: [],
    });
    expect(without?.ceoReview).toBeUndefined();
  });

  it("empty recovery pending does not wipe live approval cards", async () => {
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "m1",
      payload: {
        approval_id: "a-live",
        tool_name: "host_shell",
        arguments: {},
      },
    });

    apiGet.mockResolvedValue({
      live_running: true,
      paused: [],
      pending_interactions: [],
    });

    vi.stubGlobal("window", { __WEB__: true });

    await loadRecovery(CID);
    expect(useInteractionStore.getState().get("a-live")?.status).toBe(
      "pending",
    );
  });

  it("empty recovery pending settles confirmed server-origin hot cards", async () => {
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "m1",
      origin: "server",
      payload: {
        approval_id: "a-done",
        tool_name: "file_write",
        arguments: {},
      },
    });

    apiGet.mockResolvedValue({
      live_running: true,
      paused: [],
      pending_interactions: [],
    });

    vi.stubGlobal("window", { __WEB__: true });

    await loadRecovery(CID);
    const done = useInteractionStore.getState().get("a-done");
    expect(done?.status).toBe("resolved");
    expect(done?.settledElsewhere).toBe(true);
  });

  it("sidecar live + empty cloud pending keeps local approval cards", async () => {
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "m1",
      origin: "sidecar",
      payload: {
        approval_id: "a-sidecar",
        tool_name: "host_shell",
        arguments: {},
      },
    });

    const recoveryIpc = vi.fn(async () => ({
      liveRunning: true,
      turnId: "turn-1",
      unsynced: [],
      paused: [],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    await loadRecovery(CID);
    expect(useInteractionStore.getState().get("a-sidecar")?.status).toBe(
      "pending",
    );
  });

  it("cloud live + sidecar idle keeps host_shell approval (D6)", async () => {
    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      turnId: undefined,
      unsynced: [],
      paused: [],
    }));
    apiGet.mockResolvedValue({
      live_running: true,
      paused: [],
      pending_interactions: [
        {
          kind: "approval",
          id: "a-cloud-host",
          message_id: "m-cloud",
          payload: {
            approval_id: "a-cloud-host",
            tool_name: "host_shell",
            arguments: { command: "Get-CimInstance Win32_VideoController" },
          },
        },
      ],
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    const r = await loadRecovery(CID);
    expect(r.cloudLive).toBe(true);
    expect(r.cloudKnown).toBe(true);
    expect(r.sidecarLive).toBe(false);
    expect(useInteractionStore.getState().get("a-cloud-host")?.status).toBe(
      "pending",
    );
  });

  it("cloud failure + sidecar idle keeps hot approval cards (unknown ≠ idle)", async () => {
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "m1",
      payload: {
        approval_id: "a-unknown",
        tool_name: "host_shell",
        arguments: {},
      },
    });

    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [],
      paused: [],
    }));
    apiGet.mockRejectedValue(new Error("network down"));

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    const r = await loadRecovery(CID);
    expect(r.cloudKnown).toBe(false);
    expect(r.cloudLive).toBe(false);
    expect(r.sidecarLive).toBe(false);
    expect(useInteractionStore.getState().get("a-unknown")?.status).toBe(
      "pending",
    );
  });

  it("neither engine live orphans hot approval cards", async () => {
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "m1",
      origin: "sidecar",
      payload: {
        approval_id: "a-stale",
        tool_name: "host_shell",
        arguments: {},
      },
    });

    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [],
      paused: [],
    }));
    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    await loadRecovery(CID);
    expect(useInteractionStore.getState().get("a-stale")?.status).toBe(
      "orphaned",
    );
  });

  it("在飞的空快照不清它发起后才浮现的 live 卡（pause 抢跑竞态）", async () => {
    let settleGet!: (res: unknown) => void;
    apiGet.mockImplementation(
      () =>
        new Promise((resolve) => {
          settleGet = resolve;
        }),
    );
    vi.stubGlobal("window", { __WEB__: true });

    // GET 已上路，此后才收到 live pause —— 这次快照读不到它的 durable 帧。
    const loading = loadRecovery(CID);
    await Promise.resolve();
    usePausedTurnStore
      .getState()
      .addLiveResume(liveResume("m-live", "cp-live"));
    settleGet({ live_running: false, paused: [], pending_interactions: [] });

    const r = await loading;
    expect(r.pausedCount).toBe(0);
    const pending = usePausedTurnStore.getState().pending;
    expect(pending).toHaveLength(1);
    expect(pending[0]?.messageId).toBe("m-live");
    expect(pending[0]?.checkpointId).toBe("cp-live");
  });

  it("卡浮现之后才发起的空快照是权威的：陈旧壳清掉", async () => {
    // 另一端已拍板 → 服务端帧被消费。帧是「先落盘再发 *_required」的，所以一次晚于
    // 这张卡浮现的快照必然看得见它——回空 = 真没了。
    usePausedTurnStore
      .getState()
      .addLiveResume(liveResume("m-gone", "cp-gone"));

    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });
    vi.stubGlobal("window", { __WEB__: true });

    await loadRecovery(CID);
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("云请求失败不得清 server 壳（未知 ≠ 帧没了）", async () => {
    usePausedTurnStore.getState().addLiveResume(liveResume("m-cloud", "cp-c"));

    const recoveryIpc = vi.fn(async () => ({
      liveRunning: false,
      unsynced: [],
      paused: [],
    }));
    apiGet.mockRejectedValue(new Error("network down"));

    vi.stubGlobal("window", {
      __WEB__: false,
      sidecarApi: { recovery: recoveryIpc },
    });

    const r = await loadRecovery(CID);
    expect(r.cloudKnown).toBe(false);
    // 本机侧答了「没有挂起」，但这张壳的帧在云上——那一路没问到，不许清。
    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
  });

  it("GET 在飞时新热卡不被空 pending 误清", async () => {
    let settleGet!: (res: unknown) => void;
    apiGet.mockImplementation(
      () =>
        new Promise((resolve) => {
          settleGet = resolve;
        }),
    );
    vi.stubGlobal("window", { __WEB__: true });

    const loading = loadRecovery(CID);
    await Promise.resolve();
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "m-inflight",
      origin: "server",
      payload: {
        approval_id: "a-inflight",
        tool_name: "host_shell",
        arguments: {},
      },
    });
    settleGet({ live_running: false, paused: [], pending_interactions: [] });

    await loading;
    expect(useInteractionStore.getState().get("a-inflight")?.status).toBe(
      "pending",
    );
  });

  it("切回假冷卡打终态且不物理删", async () => {
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-gone",
      origin: "server",
      payload: { checkpoint_id: "cp-gone", question: "继续？" },
    });

    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });
    vi.stubGlobal("window", { __WEB__: true });

    await loadRecovery(CID);
    const gone = useInteractionStore.getState().get("cp-gone");
    expect(gone).toBeDefined();
    expect(gone?.status).toBe("resolved");
    expect(gone?.resumeSettled).toBeDefined();
  });

  it("其它会话的壳不受本会话快照影响", async () => {
    usePausedTurnStore.getState().addLiveResume({
      ...liveResume("m-other", "cp-o"),
      conversationId: OTHER,
    });

    apiGet.mockResolvedValue({
      live_running: false,
      paused: [],
      pending_interactions: [],
    });
    vi.stubGlobal("window", { __WEB__: true });

    await loadRecovery(CID);
    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
  });
});

describe("shouldHydrateLocalRecovery", () => {
  it("is true for sidecar live / unsynced / paused", () => {
    expect(
      shouldHydrateLocalRecovery({
        sidecarLive: false,
        cloudLive: true,
        cloudKnown: true,
        pausedCount: 0,
        unsynced: [],
      }),
    ).toBe(false);
    expect(
      shouldHydrateLocalRecovery({
        sidecarLive: true,
        cloudLive: false,
        cloudKnown: true,
        pausedCount: 0,
        unsynced: [],
      }),
    ).toBe(true);
    expect(
      shouldHydrateLocalRecovery({
        sidecarLive: false,
        cloudLive: false,
        cloudKnown: true,
        pausedCount: 1,
        unsynced: [],
      }),
    ).toBe(true);
  });
});
