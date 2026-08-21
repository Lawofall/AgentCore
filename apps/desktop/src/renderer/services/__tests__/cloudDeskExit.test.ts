// @vitest-environment jsdom

import {
  __clearMemoryUiStorageForTests,
  __setUiStorageBackendForTests,
} from "@/lib/uiStorage";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => [
    { id: "c-folder", folderId: "f1" },
    { id: "c-sibling", folderId: "f1" },
    { id: "c-bare", folderId: null },
  ],
}));

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => true,
}));

vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyActionError: vi.fn(),
  notifyInfo: vi.fn(),
  notifyWarning: vi.fn(),
}));

vi.mock("@/services/workspace", () => ({
  exportWorkspaceZip: vi.fn(),
  exportWorkspaceToLocal: vi.fn(),
}));

vi.mock("@/services/mergeLandingDiff", () => ({
  prepareMergeLandingDiff: vi.fn(),
}));

vi.mock("@/services/mergeArtifactsOnly", () => ({
  resolveMergeArtifactRefs: vi.fn(),
  writeArtifactsToLanding: vi.fn(),
}));

const { openSession } = vi.hoisted(() => ({
  openSession: vi.fn(),
}));

vi.mock("@/stores/mergeLandingReview", () => ({
  useMergeLandingReviewStore: {
    getState: () => ({
      openSession: (...args: unknown[]) => openSession(...args),
    }),
  },
}));

vi.mock("@/stores/conversation", () => ({
  getRuntime: vi.fn(() => ({ messages: [{ role: "assistant", id: "a1" }] })),
  lastAssistantProjectionId: vi.fn(() => "a1"),
}));

vi.mock("@/stores/execution", () => ({
  useExecutionStore: {
    getState: vi.fn(() => ({ byId: {} })),
  },
}));

import { notifyInfo, notifySuccess } from "@/lib/toast";
import {
  mergeArtifactsOnlyToLanding,
  mergeBackToLanding,
  peekMergeLanding,
  registerMergeLanding,
} from "@/services/cloudDeskExit";
import {
  resolveMergeArtifactRefs,
  writeArtifactsToLanding,
} from "@/services/mergeArtifactsOnly";
import { prepareMergeLandingDiff } from "@/services/mergeLandingDiff";
import { useExecutionStore } from "@/stores/execution";

const prepareMock = prepareMergeLandingDiff as unknown as ReturnType<
  typeof vi.fn
>;
const resolveRefsMock = resolveMergeArtifactRefs as unknown as ReturnType<
  typeof vi.fn
>;
const writeArtifactsMock = writeArtifactsToLanding as unknown as ReturnType<
  typeof vi.fn
>;
const execGetState = useExecutionStore.getState as unknown as ReturnType<
  typeof vi.fn
>;
const notifyInfoMock = notifyInfo as unknown as ReturnType<typeof vi.fn>;
const notifySuccessMock = notifySuccess as unknown as ReturnType<typeof vi.fn>;

const memory = new Map<string, string>();

describe("cloudDeskExit · merge landing", () => {
  beforeEach(() => {
    memory.clear();
    openSession.mockReset();
    prepareMock.mockReset();
    resolveRefsMock.mockReset();
    writeArtifactsMock.mockReset();
    notifyInfoMock.mockReset();
    notifySuccessMock.mockReset();
    execGetState.mockReturnValue({ byId: {} });
    __setUiStorageBackendForTests({
      getItem: (key) => memory.get(key) ?? null,
      setItem: (key, value) => {
        memory.set(key, value);
      },
      removeItem: (key) => {
        memory.delete(key);
      },
      keys: () => [...memory.keys()],
    });
    window.fsApi = {
      addRoot: vi.fn().mockResolvedValue({
        ok: true,
        root: { id: "root-x", name: "landing" },
      }),
      workspaceOp: vi.fn(),
    } as unknown as typeof window.fsApi;
  });

  afterEach(() => {
    __setUiStorageBackendForTests(null);
    __clearMemoryUiStorageForTests();
  });

  it("registerMergeLanding persists preference for folder scope", async () => {
    const result = await registerMergeLanding("c-folder");
    expect(result.ok).toBe(true);
    expect(
      peekMergeLanding("c-folder", [{ id: "root-x", name: "landing" }]),
    ).toEqual({
      rootId: "root-x",
      rootName: "landing",
      missing: false,
    });
    // Sibling conversation on same folder sees the same landing.
    expect(
      peekMergeLanding("c-sibling", [{ id: "root-x", name: "landing" }]),
    ).toEqual({
      rootId: "root-x",
      rootName: "landing",
      missing: false,
    });
  });

  it("marks landing missing when root not in list", async () => {
    await registerMergeLanding("c-bare");
    expect(peekMergeLanding("c-bare", [])).toEqual({
      rootId: "root-x",
      rootName: null,
      missing: true,
    });
  });

  it("mergeBackToLanding prepares Diff and opens review (not whole-tree)", async () => {
    await registerMergeLanding("c-folder");
    prepareMock.mockResolvedValue({
      conversationId: "c-folder",
      rootId: "root-x",
      rootName: "landing",
      rows: [],
      bytesByPath: {},
      skippedOversized: [],
      skippedUnreadable: [],
      truncated: false,
    });
    openSession.mockResolvedValue({
      applied: true,
      summaryLabel: "1 已写入",
    });

    const result = await mergeBackToLanding("c-folder", [
      { id: "root-x", name: "landing" },
    ]);
    expect(result).toEqual({ ok: true });
    expect(prepareMock).toHaveBeenCalledWith("c-folder", "root-x", "landing");
    expect(openSession).toHaveBeenCalled();
  });

  it("mergeBackToLanding：评审 busy → 提示且不成功", async () => {
    await registerMergeLanding("c-folder");
    prepareMock.mockResolvedValue({
      conversationId: "c-folder",
      rootId: "root-x",
      rootName: "landing",
      rows: [],
      bytesByPath: {},
      skippedOversized: [],
      skippedUnreadable: [],
      truncated: false,
    });
    openSession.mockResolvedValue({ applied: false, reason: "busy" });

    const result = await mergeBackToLanding("c-folder", [
      { id: "root-x", name: "landing" },
    ]);
    expect(result).toEqual({
      ok: false,
      reason: "unavailable",
      message: "已有合回评审进行中",
    });
    expect(notifyInfoMock).toHaveBeenCalledWith("已有合回评审进行中");
    expect(notifySuccessMock).not.toHaveBeenCalled();
  });

  it("mergeArtifactsOnlyToLanding：无产物 → 提示且不写盘", async () => {
    await registerMergeLanding("c-folder");
    resolveRefsMock.mockReturnValue([]);

    const result = await mergeArtifactsOnlyToLanding("c-folder", [
      { id: "root-x", name: "landing" },
    ]);
    expect(result).toEqual({
      ok: false,
      reason: "unavailable",
      message: "本回合无交付产物",
    });
    expect(notifyInfoMock).toHaveBeenCalledWith("本回合无交付产物");
    expect(writeArtifactsMock).not.toHaveBeenCalled();
  });

  it("mergeArtifactsOnlyToLanding：有产物 → 只写那些路径", async () => {
    await registerMergeLanding("c-folder");
    resolveRefsMock.mockReturnValue([{ path: "out/a.md" }]);
    writeArtifactsMock.mockResolvedValue({
      written: ["out/a.md"],
      skippedExisting: [],
      errors: [],
    });

    const result = await mergeArtifactsOnlyToLanding("c-folder", [
      { id: "root-x", name: "landing" },
    ]);
    expect(result).toEqual({ ok: true });
    expect(writeArtifactsMock).toHaveBeenCalledWith({
      conversationId: "c-folder",
      rootId: "root-x",
      refs: [{ path: "out/a.md" }],
    });
    expect(notifySuccessMock).toHaveBeenCalled();
    expect(prepareMock).not.toHaveBeenCalled();
  });

  it("mergeArtifactsOnlyToLanding：传入 refs 则不读 latest delivery", async () => {
    await registerMergeLanding("c-folder");
    resolveRefsMock.mockReturnValue([]);
    writeArtifactsMock.mockResolvedValue({
      written: ["card.md"],
      skippedExisting: [],
      errors: [],
    });

    const result = await mergeArtifactsOnlyToLanding(
      "c-folder",
      [{ id: "root-x", name: "landing" }],
      [{ path: "card.md" }],
    );
    expect(result).toEqual({ ok: true });
    expect(resolveRefsMock).not.toHaveBeenCalled();
    expect(writeArtifactsMock).toHaveBeenCalledWith({
      conversationId: "c-folder",
      rootId: "root-x",
      refs: [{ path: "card.md" }],
    });
  });
});
