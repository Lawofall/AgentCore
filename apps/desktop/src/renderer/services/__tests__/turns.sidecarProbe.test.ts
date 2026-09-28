import { StreamError } from "@/lib/errors";
import { SIDECAR_OCCUPY_FAILED_CODE } from "@/services/streamPathReason";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 隔离断言 sendTurn / runRegenerate / runResume「探活 → 路由」这一段的可观察契约：
// sendTurn——探活 ok 走 sidecar；探活失败 / 启动期失败 / 占位失败出横幅不走云；
// 中途失败(!recoverable)不自动降级；本机绑定但引擎接不住则停发；纯云会话走云。
// runRegenerate——与 sendTurn 同形。
// runResume——探活 ok 走 sidecar 续跑、探活失败保留续跑卡 + 出横幅（无 banner retry）、
// 404/PAUSED_TURN_NOT_FOUND 丢卡、绝不降级走云（本机帧云端没有）。
// 协作者全 mock；conversation / pausedTurn store 用真实，使 stillOptimistic / 截断 / 帧认领忠实。
vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
  bumpConversationCache: vi.fn(),
  restoreConversationCache: vi.fn(),
  syncConversationListPreview: vi.fn(),
}));
vi.mock("@/services/sidecarRouting", () => {
  const resolveSidecarRoot = vi.fn();
  const resolveConversationLocalTarget = vi.fn((_conversationId: string) =>
    Promise.resolve(null),
  );
  return {
    resolveSidecarRoot,
    resolveConversationLocalTarget,
    resolveLocalBind: vi.fn(async (conversationId: string) => {
      const t = (await resolveConversationLocalTarget(conversationId)) as {
        rootId: string;
        subpath: string;
      } | null;
      return t
        ? { kind: "live" as const, rootId: t.rootId, subpath: t.subpath }
        : { kind: "unbound" as const };
    }),
    resolveNewTurnBind: vi.fn(async (conversationId: string) => {
      const t = (await resolveSidecarRoot(conversationId)) as {
        rootId: string;
        subpath: string;
      } | null;
      return t
        ? { kind: "live" as const, rootId: t.rootId, subpath: t.subpath }
        : { kind: "unbound" as const };
    }),
    liveSidecarTarget: (bind: {
      kind: string;
      rootId?: string;
      subpath?: string;
    }) =>
      bind.kind === "live" && typeof bind.rootId === "string"
        ? { rootId: bind.rootId, subpath: bind.subpath ?? "" }
        : null,
    localBindSendBlock: (bind: {
      kind: string;
      rootId?: string;
      absPath?: string;
      reason?: string;
    }) => {
      if (bind.kind === "stale") {
        const path = bind.absPath?.trim();
        return {
          reason: "root_stale",
          rootId: bind.rootId ?? "",
          message: path
            ? `这个文件夹已经不在这台电脑上：${path}。请在工作区芯片里重新选择它所在的位置。`
            : "这个文件夹已经不在这台电脑上。请在工作区芯片里重新选择它所在的位置。",
        };
      }
      if (bind.kind === "absent") {
        return {
          reason: "root_absent",
          rootId: bind.rootId ?? "",
          message:
            "这个文件夹不在这台电脑上。请在工作区芯片里重新选择它所在的位置。",
        };
      }
      if (bind.kind === "engine_off") {
        return {
          reason: bind.reason === "switch_off" ? "switch_off" : "no_local_engine",
          rootId: bind.rootId ?? "",
          message:
            bind.reason === "switch_off"
              ? "本机执行已关闭，云端不会改这个文件夹。请先允许本机执行后再发，或把对话改到云端。"
              : "这份文件在本机。请在持有该文件夹的电脑上打开客户端后再发。",
        };
      }
      return null;
    },
    getActiveSidecarTarget: vi.fn(() => null),
    setActiveSidecarTurn: vi.fn(),
    isSidecarEnabled: vi.fn(() => true),
  };
});
vi.mock("@/services/turns/midFlight", () => ({
  sendMidFlightMessage: vi.fn(),
}));
vi.mock("@/services/sidecarHealth", () => ({
  probeSidecar: vi.fn(),
  markSidecarUnhealthy: vi.fn(),
  clearSidecarHealth: vi.fn(),
}));
vi.mock("@/lib/capabilities", () => ({
  hasLocalEngine: vi.fn(() => true),
}));
vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));
vi.mock("@/services/streamConversation", () => ({
  attachConversation: vi.fn(),
  regenerateConversation: vi.fn(),
  resumeConversation: vi.fn(),
  streamConversation: vi.fn(() => Promise.resolve()),
}));
vi.mock("@/services/streamConversationViaSidecar", () => ({
  resumeConversationViaSidecar: vi.fn(),
  streamConversationViaSidecar: vi.fn(),
}));
vi.mock("@/services/messages", () => ({ loadLatestWindow: vi.fn() }));
// notifyError 由 stream 错误路径间接引入；引擎不可用走横幅，不弹 toast。
vi.mock("@/lib/toast", () => ({ notifyInfo: vi.fn(), notifyError: vi.fn() }));

import { hasLocalEngine } from "@/lib/capabilities";
import { logEvent } from "@/lib/log";
import { notifyInfo } from "@/lib/toast";
import {
  clearSidecarHealth,
  markSidecarUnhealthy,
  probeSidecar,
} from "@/services/sidecarHealth";
import {
  getActiveSidecarTarget,
  isSidecarEnabled,
  resolveConversationLocalTarget,
  resolveLocalBind,
  resolveNewTurnBind,
  resolveSidecarRoot,
  setActiveSidecarTurn,
} from "@/services/sidecarRouting";
import {
  regenerateConversation,
  resumeConversation,
  streamConversation,
} from "@/services/streamConversation";
import {
  resumeConversationViaSidecar,
  streamConversationViaSidecar,
} from "@/services/streamConversationViaSidecar";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import { useConversationStore } from "@/stores/conversation";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import { runRegenerate, runResume, sendTurn } from "../turns";

const resolveSidecarRootMock = vi.mocked(resolveSidecarRoot);
const resolveNewTurnBindMock = vi.mocked(resolveNewTurnBind);
const resolveLocalTargetMock = vi.mocked(resolveConversationLocalTarget);
const resolveLocalBindMock = vi.mocked(resolveLocalBind);
const getActiveSidecarTargetMock = vi.mocked(getActiveSidecarTarget);
const isSidecarEnabledMock = vi.mocked(isSidecarEnabled);
const hasLocalEngineMock = vi.mocked(hasLocalEngine);
const logEventMock = vi.mocked(logEvent);
const probeSidecarMock = vi.mocked(probeSidecar);
const markSidecarUnhealthyMock = vi.mocked(markSidecarUnhealthy);
const clearSidecarHealthMock = vi.mocked(clearSidecarHealth);
const streamConversationMock = vi.mocked(streamConversation);
const streamViaSidecarMock = vi.mocked(streamConversationViaSidecar);
const sendMidFlightMock = vi.mocked(sendMidFlightMessage);
const setActiveSidecarTurnMock = vi.mocked(setActiveSidecarTurn);
const regenerateConversationMock = vi.mocked(regenerateConversation);
const resumeConversationMock = vi.mocked(resumeConversation);
const resumeViaSidecarMock = vi.mocked(resumeConversationViaSidecar);
const notifyInfoMock = vi.mocked(notifyInfo);

const TARGET = { rootId: "r1", subpath: "" };

function spec() {
  return {
    conversationId: "c1",
    content: "hi",
    attachments: [],
    optimisticUserId: "opt1",
  };
}

/** Seed the optimistic user bubble sendTurn expects: stillOptimistic = true → a
 *  fresh attempt (not regenerate-from-persisted). */
function seedOptimisticUser(): void {
  useConversationStore.getState().addMessage(
    {
      id: "opt1",
      role: "user",
      content: "hi",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    },
    "c1",
  );
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  usePausedTurnStore.setState({ pending: [] });
  vi.clearAllMocks();
  resolveNewTurnBindMock.mockImplementation(async (conversationId: string) => {
    const t = await resolveSidecarRootMock(conversationId);
    return t
      ? { kind: "live" as const, rootId: t.rootId, subpath: t.subpath }
      : { kind: "unbound" as const };
  });
  resolveLocalBindMock.mockImplementation(async (conversationId: string) => {
    const t = await resolveLocalTargetMock(conversationId);
    return t
      ? { kind: "live" as const, rootId: t.rootId, subpath: t.subpath }
      : { kind: "unbound" as const };
  });
  streamConversationMock.mockResolvedValue(undefined);
  sendMidFlightMock.mockResolvedValue({
    kind: "received",
    interjectionId: "ij-busy",
  });
  regenerateConversationMock.mockResolvedValue(undefined);
  resolveLocalTargetMock.mockResolvedValue(null);
  getActiveSidecarTargetMock.mockReturnValue(null);
  isSidecarEnabledMock.mockReturnValue(true);
  hasLocalEngineMock.mockReturnValue(true);
  seedOptimisticUser();
});

afterEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  usePausedTurnStore.setState({ pending: [] });
});

describe("sendTurn — 探活路由（引擎不可用报错）", () => {
  it("探活通过 → 走本地 sidecar，不碰云链路", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockImplementation(async (opts) => {
      if (opts.turnCommit) opts.turnCommit.committed = true;
      useConversationStore.getState().appendToLastMessage("好", "c1");
      return undefined as never;
    });

    await sendTurn(spec());

    expect(probeSidecarMock).toHaveBeenCalledTimes(1);
    expect(streamViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({ conversationId: "c1", rootId: "r1" }),
    );
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBe(
      "sidecar",
    );
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({ via: "sidecar", reason: "probe_ok" }),
    );
  });

  it("探活失败 → 诊断横幅，不走云", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: false,
      probed: true,
      detail: "本地引擎启动失败：spawn uv ENOENT",
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(streamViaSidecarMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.messages).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "spawn uv ENOENT",
    );
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({
        via: "sidecar",
        reason: "probe_unhealthy",
      }),
    );
  });

  it("探活通过但回合启动期失败(recoverable) → 标坏 + 横幅，不走云", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, {
        serverMessage: "拉不起",
        recoverable: true,
      }),
    );

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(markSidecarUnhealthyMock).toHaveBeenCalledWith(TARGET, "拉不起");
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.messages).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.executionVia).toBe(
      "sidecar",
    );
    expect(useConversationStore.getState().byId.c1?.error).toContain("拉不起");
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({
        via: "sidecar",
        reason: "start_failed",
      }),
    );
  });

  it("占位失败 → 横幅，不走云、不标坏", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, {
        code: SIDECAR_OCCUPY_FAILED_CODE,
        recoverable: true,
        serverMessage: "云端占位失败，本地回合未启动",
      }),
    );

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(markSidecarUnhealthyMock).not.toHaveBeenCalled();
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "云端占位失败",
    );
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({
        via: "sidecar",
        reason: "occupy_failed",
      }),
    );
  });

  it("中途失败(!recoverable) → 不自动降级、不标坏（照常出横幅）", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, {
        serverMessage: "中途崩",
        recoverable: false,
      }),
    );

    await sendTurn(spec());

    expect(markSidecarUnhealthyMock).not.toHaveBeenCalled();
    expect(streamConversationMock).not.toHaveBeenCalled();
  });

  it("忙槽 → 改走本机插队，不画失败、不走云", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, {
        code: "sidecar_turn_busy",
        recoverable: false,
        serverMessage: "当前还有回合在进行，请稍候或先停止后再继续",
      }),
    );

    const result = await sendTurn(spec());

    expect(result).toEqual({ unstartedRefusal: false });
    expect(sendMidFlightMock).toHaveBeenCalledWith(
      "c1",
      "hi",
      undefined,
      "steer",
      undefined,
      { sidecarTarget: TARGET, userMessageId: "opt1" },
    );
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(markSidecarUnhealthyMock).not.toHaveBeenCalled();
    expect(setActiveSidecarTurnMock).toHaveBeenCalledWith("c1", "r1", "");
    const rt = useConversationStore.getState().byId.c1;
    expect(rt?.error).toBeFalsy();
    expect(rt?.isGenerating).toBe(true);
    expect(rt?.turnPhase).toBe("streaming");
    expect(rt?.messages.some((m) => m.id === "opt1")).toBe(true);
    expect(rt?.messages.some((m) => m.role === "assistant")).toBe(false);
  });

  it("bad 缓存命中(!probed) → 横幅，不走云", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    // 该根本会话已探明坏：probeSidecar 命中缓存（probed:false）。
    probeSidecarMock.mockResolvedValue({
      healthy: false,
      probed: false,
      detail: "本地引擎启动失败：spawn uv ENOENT",
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(streamViaSidecarMock).not.toHaveBeenCalled();
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.messages).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "spawn uv ENOENT",
    );
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({
        via: "sidecar",
        reason: "probe_cache_bad",
      }),
    );
  });

  it("bad 缓存无 detail 仍报错不走云", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: false,
      probed: false,
      detail: null,
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.messages).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "本地引擎未能启动",
    );
  });
  it("开关关 + 绑本机 → 停发，不走云", async () => {
    resolveNewTurnBindMock.mockResolvedValue({
      kind: "engine_off",
      rootId: "r1",
      subpath: "",
      reason: "switch_off",
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(probeSidecarMock).not.toHaveBeenCalled();
    expect(streamViaSidecarMock).not.toHaveBeenCalled();
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "本机执行已关闭",
    );
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({ via: "sidecar", reason: "switch_off" }),
    );
  });

  it("绑本机 + 附件仍走 sidecar，并把附件转给本机链路", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockResolvedValue(undefined as never);
    const attachments = [
      {
        name: "f.txt",
        path: "docs/f.txt",
        text: "x",
        truncated: false,
        kind: "file" as const,
        workspace_path: "docs/f.txt",
      },
    ];

    await sendTurn({
      ...spec(),
      attachments,
    });

    expect(resolveSidecarRootMock).toHaveBeenCalled();
    expect(probeSidecarMock).toHaveBeenCalledTimes(1);
    expect(streamViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: "c1",
        rootId: "r1",
        attachments,
      }),
    );
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBe(
      "sidecar",
    );
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({ via: "sidecar", reason: "probe_ok" }),
    );
  });

  it("附件+点名仍走 sidecar（点名不改路）", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockResolvedValue(undefined as never);
    const attachments = [
      {
        name: "f.txt",
        path: "docs/f.txt",
        text: "x",
        truncated: false,
        kind: "file" as const,
      },
    ];
    const agentMentions = [{ agent_id: "a1", role: "研究员" }];

    await sendTurn({
      ...spec(),
      attachments,
      agentMentions,
    });

    expect(streamViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({ attachments, agentMentions }),
    );
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("点名不退云：绑本机 → 走 sidecar，并把点名转给本机链路", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockResolvedValue(undefined as never);
    const agentMentions = [{ agent_id: "a1", role: "研究员" }];

    await sendTurn({
      ...spec(),
      agentMentions,
    });

    expect(resolveSidecarRootMock).toHaveBeenCalled();
    expect(probeSidecarMock).toHaveBeenCalledTimes(1);
    expect(streamViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: "c1",
        rootId: "r1",
        agentMentions,
      }),
    );
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBe(
      "sidecar",
    );
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({ via: "sidecar", reason: "probe_ok" }),
    );
  });

  it("纯云会话（无本机绑定）→ executionVia 为 null", async () => {
    resolveSidecarRootMock.mockResolvedValue(null);
    resolveLocalTargetMock.mockResolvedValue(null);

    await sendTurn(spec());

    expect(streamConversationMock).toHaveBeenCalledTimes(1);
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("死绑定 → 中文横幅，不 probe、不走云", async () => {
    resolveNewTurnBindMock.mockResolvedValue({
      kind: "stale",
      rootId: "r1",
      subpath: "",
      absPath: "/Users/zoo/J-",
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(probeSidecarMock).not.toHaveBeenCalled();
    expect(streamConversationMock).not.toHaveBeenCalled();
    expect(streamViaSidecarMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "这个文件夹已经不在这台电脑上",
    );
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "/Users/zoo/J-",
    );
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "turn.stream_path",
      expect.objectContaining({
        via: "sidecar",
        reason: "root_stale",
      }),
    );
  });
});

describe("runRegenerate — 探活路由（与 sendTurn 同形）", () => {
  function seedPersistedTurn(): void {
    useConversationStore.setState({ currentConversationId: "c1", byId: {} });
    const conv = useConversationStore.getState();
    conv.addMessage(
      {
        id: "u1",
        role: "user",
        content: "hi",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      },
      "c1",
    );
    conv.addMessage(
      {
        id: "a1",
        role: "assistant",
        content: "old",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      },
      "c1",
    );
  }

  beforeEach(() => {
    seedPersistedTurn();
  });

  it("探活通过 → 走本地 sidecar regenerate，不碰云端 regenerate", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockResolvedValue(undefined as never);

    await runRegenerate("u1");

    expect(streamViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: "c1",
        rootId: "r1",
        regenerate: true,
        optimisticUserId: "u1",
        content: "hi",
      }),
    );
    expect(regenerateConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBe(
      "sidecar",
    );
  });

  it("探活失败 → 诊断横幅，不走云端 regenerate", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: false,
      probed: true,
      detail: "env down",
    });

    await runRegenerate("u1");

    expect(streamViaSidecarMock).not.toHaveBeenCalled();
    expect(regenerateConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "env down",
    );
  });

  it("探活通过但回合启动期失败(recoverable) → 标坏 + 横幅，不走云 regenerate", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, {
        recoverable: true,
        serverMessage: "handshake failed",
      }),
    );

    await runRegenerate("u1");

    expect(markSidecarUnhealthyMock).toHaveBeenCalled();
    expect(regenerateConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBe(
      "sidecar",
    );
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "handshake failed",
    );
  });

  it("占位失败 → 横幅，不走云 regenerate、不标坏", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, {
        code: SIDECAR_OCCUPY_FAILED_CODE,
        recoverable: true,
        serverMessage: "云端占位失败，本地回合未启动",
      }),
    );

    await runRegenerate("u1");

    expect(markSidecarUnhealthyMock).not.toHaveBeenCalled();
    expect(regenerateConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.executionVia).toBeNull();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "云端占位失败",
    );
  });

  it("中途失败(!recoverable) → 不自动降级走云", async () => {
    resolveSidecarRootMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    streamViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, { recoverable: false }),
    );

    await runRegenerate("u1");

    expect(regenerateConversationMock).not.toHaveBeenCalled();
    expect(markSidecarUnhealthyMock).not.toHaveBeenCalled();
  });
});

/** 构造一个 sidecar 暂停帧（ask_user），续跑测试用：字段齐全、内容最小。 */
function pendingFrame(messageId: string, conversationId = "c1"): PendingResume {
  return {
    messageId,
    conversationId,
    checkpointId: "ck1",
    kind: "ask_user",
    userMessage: "原始请求",
    userMessageId: "u-orig",
    steps: [],
    pending: [],
    question: "",
    questions: [],
    intent: "decision",
    origin: "sidecar",
  };
}

describe("runResume — 续跑探活（不降级、本机帧只在本地）", () => {
  beforeEach(() => {
    useConversationStore.setState({ currentConversationId: "c1", byId: {} });
    usePausedTurnStore.setState({ pending: [pendingFrame("m1")] });
    // Seed the paused assistant so resume reuses it (Option A) instead of fallback-create.
    const conv = useConversationStore.getState();
    conv.addMessage({
      id: "u-orig",
      role: "user",
      content: "原始请求",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    conv.addMessage({
      id: "client-paused",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: false,
      serverMessageId: "m1",
      finishReason: "paused",
    });
  });

  it("探活通过 → 本地 sidecar 续跑、认领续跑卡", async () => {
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    resumeViaSidecarMock.mockResolvedValue(undefined as never);

    const before = useConversationStore
      .getState()
      .byId.c1?.messages.filter((m) => m.role === "assistant").length;

    await runResume("m1", "continue", "");

    expect(resolveSidecarRootMock).not.toHaveBeenCalled();
    expect(resumeViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({
        messageId: "m1",
        rootId: "r1",
        userMessageId: "u-orig",
      }),
    );
    expect(resumeConversationMock).not.toHaveBeenCalled();
    expect(usePausedTurnStore.getState().pending).toHaveLength(0); // 帧已认领
    const assistants = useConversationStore
      .getState()
      .byId.c1?.messages.filter((m) => m.role === "assistant");
    expect(assistants).toHaveLength(before);
    expect(assistants[0].id).toBe("client-paused");
    expect(assistants[0].isStreaming).toBe(true);
  });

  it("偏好强制关 + origin=sidecar → 仍跟本地事实续跑（忽略 off）", async () => {
    isSidecarEnabledMock.mockReturnValue(false);
    resolveSidecarRootMock.mockResolvedValue(null);
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    resumeViaSidecarMock.mockResolvedValue(undefined as never);

    await runResume("m1", "continue", "");

    expect(resolveSidecarRootMock).not.toHaveBeenCalled();
    expect(resolveLocalTargetMock).toHaveBeenCalledWith("c1");
    expect(resumeViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({ rootId: "r1", messageId: "m1" }),
    );
    expect(resumeConversationMock).not.toHaveBeenCalled();
  });

  it("活回合 active target 优先于 resolveConversationLocalTarget", async () => {
    getActiveSidecarTargetMock.mockReturnValue({
      rootId: "r-active",
      subpath: "scratch",
      turnId: "t1",
    });
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    resumeViaSidecarMock.mockResolvedValue(undefined as never);

    await runResume("m1", "continue", "");

    expect(resumeViaSidecarMock).toHaveBeenCalledWith(
      expect.objectContaining({
        rootId: "r-active",
        subpath: "scratch",
      }),
    );
  });

  it("探活失败 → 保留续跑卡 + 出横幅，绝不降级走云", async () => {
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: false,
      probed: true,
      detail: "venv 损坏",
    });

    await expect(runResume("m1", "continue", "")).rejects.toThrow(
      /sidecar probe failed/,
    );

    expect(resumeViaSidecarMock).not.toHaveBeenCalled();
    expect(resumeConversationMock).not.toHaveBeenCalled(); // 不降级走云（云端没这帧）
    expect(usePausedTurnStore.getState().pending).toHaveLength(1); // 续跑卡保留
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "venv 损坏",
    );
  });

  it("sidecar 帧无 target → 留卡 + 横幅，绝不降级走云", async () => {
    resolveLocalTargetMock.mockResolvedValue(null);

    await expect(runResume("m1", "continue", "")).rejects.toThrow(
      /sidecar unavailable/,
    );

    expect(probeSidecarMock).not.toHaveBeenCalled();
    expect(resumeViaSidecarMock).not.toHaveBeenCalled();
    expect(resumeConversationMock).not.toHaveBeenCalled(); // 不降级走云（云端没这帧）
    expect(usePausedTurnStore.getState().pending).toHaveLength(1); // 续跑卡保留
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "本地引擎暂不可用",
    );
  });

  it("死绑定 → 文件夹消失横幅，不 probe、不走云", async () => {
    resolveLocalBindMock.mockResolvedValue({
      kind: "stale",
      rootId: "r1",
      subpath: "",
      absPath: "/Users/zoo/J-",
    });
    getActiveSidecarTargetMock.mockReturnValue({
      rootId: "r-active",
      subpath: "",
      turnId: "t1",
    });

    await expect(runResume("m1", "continue", "")).rejects.toThrow(
      /workspace root gone/,
    );

    expect(probeSidecarMock).not.toHaveBeenCalled();
    expect(resumeViaSidecarMock).not.toHaveBeenCalled();
    expect(resumeConversationMock).not.toHaveBeenCalled();
    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "这个文件夹已经不在这台电脑上",
    );
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "/Users/zoo/J-",
    );
  });

  it("授权表没有这个 id → 重新选择横幅，不 probe、不走云", async () => {
    resolveLocalBindMock.mockResolvedValue({
      kind: "absent",
      rootId: "r1",
      subpath: "",
    });
    getActiveSidecarTargetMock.mockReturnValue({
      rootId: "r-active",
      subpath: "",
      turnId: "t1",
    });

    await expect(runResume("m1", "continue", "")).rejects.toThrow(
      /workspace root absent/,
    );

    expect(probeSidecarMock).not.toHaveBeenCalled();
    expect(resumeViaSidecarMock).not.toHaveBeenCalled();
    expect(resumeConversationMock).not.toHaveBeenCalled();
    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "这个文件夹不在这台电脑上",
    );
  });

  it("云端帧未绑本地根 → 不探活、直接走云 resume", async () => {
    resolveLocalTargetMock.mockResolvedValue(null);
    usePausedTurnStore.setState({
      pending: [{ ...pendingFrame("m1"), origin: "server" }],
    });
    resumeConversationMock.mockResolvedValue(undefined as never);

    await runResume("m1", "continue", "");

    expect(probeSidecarMock).not.toHaveBeenCalled();
    expect(resolveLocalTargetMock).not.toHaveBeenCalled();
    expect(resumeConversationMock).toHaveBeenCalledTimes(1);
    expect(resumeViaSidecarMock).not.toHaveBeenCalled();
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("云端暂停帧（origin=server）即使绑了本地根也走云 resume", async () => {
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    usePausedTurnStore.setState({
      pending: [{ ...pendingFrame("m1"), origin: "server" }],
    });
    resumeConversationMock.mockResolvedValue(undefined as never);

    await runResume("m1", "continue", "");

    expect(probeSidecarMock).not.toHaveBeenCalled();
    expect(resolveLocalTargetMock).not.toHaveBeenCalled();
    expect(resumeConversationMock).toHaveBeenCalledTimes(1);
    expect(resumeViaSidecarMock).not.toHaveBeenCalled();
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("请求被拒(404) → 丢续跑卡 + 横幅（无 retry）", async () => {
    usePausedTurnStore.setState({
      pending: [{ ...pendingFrame("m1"), origin: "server" }],
    });
    resumeConversationMock.mockRejectedValue(
      new StreamError("http", 404, {
        code: "not_found",
        serverMessage: "挂起的回合不存在或已处理",
      }),
    );

    await expect(runResume("m1", "continue", "")).rejects.toBeInstanceOf(
      StreamError,
    );

    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "挂起的回合不存在或已处理",
    );
    expect(useConversationStore.getState().byId.c1?.retry).toBeNull();
  });

  it("sidecar PAUSED_TURN_NOT_FOUND → 丢续跑卡 + 横幅（无 retry）", async () => {
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    resumeViaSidecarMock.mockRejectedValue(
      new StreamError("sidecar", undefined, {
        serverMessage: "本地引擎出错：挂起的回合不存在或已处理",
        recoverable: true,
      }),
    );

    await expect(runResume("m1", "continue", "")).rejects.toBeInstanceOf(
      StreamError,
    );

    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "挂起的回合不存在或已处理",
    );
    expect(useConversationStore.getState().byId.c1?.retry).toBeNull();
  });

  it("请求被拒(409 turn_in_progress) → 恢复续跑卡 + 明确文案（无 retry）", async () => {
    usePausedTurnStore.setState({
      pending: [{ ...pendingFrame("m1"), origin: "server" }],
    });
    resumeConversationMock.mockRejectedValue(
      new StreamError("http", 409, { code: "turn_in_progress" }),
    );

    await expect(runResume("m1", "continue", "")).rejects.toBeInstanceOf(
      StreamError,
    );

    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "回合收尾尚未完成",
    );
    expect(useConversationStore.getState().byId.c1?.retry).toBeNull();
  });

  it("流中断(network) → 不恢复续跑卡", async () => {
    usePausedTurnStore.setState({
      pending: [{ ...pendingFrame("m1"), origin: "server" }],
    });
    resumeConversationMock.mockRejectedValue(new StreamError("network"));
    // rejoinLiveTurn → attachConversation；返回 attached 表示已接手，不恢复卡。
    const { attachConversation } = await import(
      "@/services/streamConversation"
    );
    vi.mocked(attachConversation).mockResolvedValue("attached");

    await runResume("m1", "continue", "");

    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("用户 abort → 不恢复续跑卡，并离开 stopping", async () => {
    usePausedTurnStore.setState({
      pending: [{ ...pendingFrame("m1"), origin: "server" }],
    });
    // 模拟诚实停止：流已开后点停 → phase=stopping，再 RPC Abort（先于 message_end）。
    resumeConversationMock.mockImplementation(async () => {
      useConversationStore.getState().setTurnPhase("stopping", "c1");
      throw new DOMException("Aborted", "AbortError");
    });

    await runResume("m1", "continue", "");

    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.error).toBeNull();
    expect(useConversationStore.getState().byId.c1?.turnPhase).toBe("stopped");
    expect(useConversationStore.getState().byId.c1?.isGenerating).toBe(false);
  });

  it("探活失败横幅清缓存（下次续跑可重探，无 banner retry）", async () => {
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: false,
      probed: true,
      detail: null,
    });

    await expect(runResume("m1", "continue", "")).rejects.toThrow(
      /sidecar probe failed/,
    );
    expect(probeSidecarMock).toHaveBeenCalledTimes(1);
    expect(clearSidecarHealthMock).toHaveBeenCalledTimes(1);
    expect(useConversationStore.getState().byId.c1?.retry).toBeNull();
    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
  });

  it("有冷卡 + isGenerating → D9 共存：不抹 generating，直接续跑", async () => {
    useConversationStore.getState().setGenerating(true, "c1");
    resolveLocalTargetMock.mockResolvedValue(TARGET);
    probeSidecarMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
    resumeViaSidecarMock.mockResolvedValue(undefined as never);

    await runResume("m1", "continue", "");

    expect(resumeViaSidecarMock).toHaveBeenCalledTimes(1);
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
    expect(useConversationStore.getState().byId.c1?.error).toBeNull();
    // 不得因冷续跑把 live generating 抹掉。
    expect(useConversationStore.getState().byId.c1?.isGenerating).toBe(true);
  });

  it("无冷卡 + isGenerating → 仍拦截（抛错 + 横幅）", async () => {
    usePausedTurnStore.setState({ pending: [] });
    useConversationStore.getState().setGenerating(true, "c1");
    resolveLocalTargetMock.mockResolvedValue(TARGET);

    await expect(runResume("m1", "continue", "")).rejects.toThrow(
      /still generating/,
    );

    expect(resumeViaSidecarMock).not.toHaveBeenCalled();
    expect(resumeConversationMock).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId.c1?.error).toContain(
      "仍在生成中",
    );
  });
});
