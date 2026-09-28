// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(),
}));
vi.mock("@/hooks/useFolders", () => ({
  getFolders: vi.fn(() => []),
}));
vi.mock("@/lib/queryClient", () => ({
  queryClient: { getQueryData: vi.fn(() => undefined) },
}));
vi.mock("@/lib/queryKeys", () => ({
  workspaceKeys: { list: ["workspaces"] },
}));
vi.mock("@/lib/capabilities", () => ({
  hasLocalEngine: vi.fn(() => true),
}));
vi.mock("@/stores/conversation", () => ({
  getRuntime: () => ({ messages: [] }),
}));

import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { hasLocalEngine } from "@/lib/capabilities";
import {
  canConversationUseSidecar,
  resolveConversationLocalTarget,
  resolveLocalBind,
  resolveNewTurnBind,
  resolveSidecarRoot,
} from "@/services/sidecarRouting";

const getConvs = getConversations as unknown as ReturnType<typeof vi.fn>;
const getFolds = getFolders as unknown as ReturnType<typeof vi.fn>;

describe("resolveSidecarRoot（新回合路由 · 本机传统默认同侧）", () => {
  beforeEach(() => {
    vi.mocked(hasLocalEngine).mockReturnValue(true);
    getConvs.mockReset();
    getFolds.mockReset();
    getFolds.mockReturnValue([]);
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: null,
        localContainerRootId: "container",
      },
    ]);
    window.fsApi = {
      listRoots: vi
        .fn()
        .mockResolvedValue([{ id: "container", name: "AgentCore" }]),
    } as unknown as typeof window.fsApi;
  });

  it("本机绑定 → 解析 sidecar 目标", async () => {
    const target = await resolveSidecarRoot("c1");
    expect(target).toEqual({
      rootId: "container",
      subpath: "conversations/c1",
    });
    expect(window.fsApi.listRoots).toHaveBeenCalled();
  });

  it("没有本地引擎 + 本机绑定 → engine_off，不走云", async () => {
    vi.mocked(hasLocalEngine).mockReturnValue(false);
    expect(await resolveSidecarRoot("c1")).toBeNull();
    expect(await resolveNewTurnBind("c1")).toEqual({
      kind: "engine_off",
      rootId: "container",
      subpath: "conversations/c1",
    });
  });

  it("§7.2 mode=cloud 项目 → 无 sidecar target（全云）", async () => {
    getConvs.mockReturnValue([
      {
        id: "c-cloud",
        title: "t",
        folderId: "f-cloud",
        localContainerRootId: null,
      },
    ]);
    getFolds.mockReturnValue([
      {
        id: "f-cloud",
        name: "CloudProj",
        mode: "cloud",
        localRootId: null,
        localSubpath: null,
      },
    ]);
    expect(await resolveConversationLocalTarget("c-cloud")).toBeNull();
    expect(await resolveSidecarRoot("c-cloud")).toBeNull();
    expect(window.fsApi.listRoots).not.toHaveBeenCalled();
  });

  it("§7.2 mode=local → 默认同侧 sidecar", async () => {
    getConvs.mockReturnValue([
      {
        id: "c-local",
        title: "t",
        folderId: "f-local",
        localContainerRootId: null,
      },
    ]);
    getFolds.mockReturnValue([
      {
        id: "f-local",
        name: "LegacyLocal",
        mode: "local",
        localRootId: "proj-root",
        localSubpath: "",
      },
    ]);
    window.fsApi = {
      listRoots: vi
        .fn()
        .mockResolvedValue([{ id: "proj-root", name: "LegacyLocal" }]),
    } as unknown as typeof window.fsApi;

    expect(await resolveConversationLocalTarget("c-local")).toEqual({
      rootId: "proj-root",
      subpath: "",
    });
    expect(await resolveSidecarRoot("c-local")).toEqual({
      rootId: "proj-root",
      subpath: "",
    });
  });

  it("授权表没有会话上的 root id → absent，不走 sidecar", async () => {
    getConvs.mockReturnValue([
      {
        id: "c-local",
        title: "t",
        folderId: "f-local",
        localContainerRootId: null,
      },
    ]);
    getFolds.mockReturnValue([
      {
        id: "f-local",
        name: "LegacyLocal",
        mode: "local",
        localRootId: "old-root",
        localSubpath: "",
      },
    ]);
    window.fsApi = {
      listRoots: vi
        .fn()
        .mockResolvedValue([{ id: "other-root", name: "Elsewhere" }]),
    } as unknown as typeof window.fsApi;

    expect(await resolveLocalBind("c-local")).toEqual({
      kind: "absent",
      rootId: "old-root",
      subpath: "",
    });
    expect(await resolveSidecarRoot("c-local")).toBeNull();
    expect(await resolveNewTurnBind("c-local")).toEqual({
      kind: "absent",
      rootId: "old-root",
      subpath: "",
    });
  });

  it("空子路径 + listRoots.missing → stale，不走 sidecar、仍算能用本机", async () => {
    getConvs.mockReturnValue([
      {
        id: "c-local",
        title: "t",
        folderId: "f-local",
        localContainerRootId: null,
      },
    ]);
    getFolds.mockReturnValue([
      {
        id: "f-local",
        name: "LegacyLocal",
        mode: "local",
        localRootId: "proj-root",
        localSubpath: "",
      },
    ]);
    window.fsApi = {
      listRoots: vi.fn().mockResolvedValue([
        {
          id: "proj-root",
          name: "LegacyLocal",
          absPath: "/Users/zoo/J-",
          missing: true,
        },
      ]),
    } as unknown as typeof window.fsApi;

    expect(await resolveLocalBind("c-local")).toEqual({
      kind: "stale",
      rootId: "proj-root",
      subpath: "",
      absPath: "/Users/zoo/J-",
    });
    expect(await resolveConversationLocalTarget("c-local")).toBeNull();
    expect(await resolveSidecarRoot("c-local")).toBeNull();
    expect(await canConversationUseSidecar("c-local")).toBe(true);
  });

  it("scratch 子路径即使容器 missing 仍 live（可 mkdir）", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: null,
        localContainerRootId: "container",
      },
    ]);
    window.fsApi = {
      listRoots: vi.fn().mockResolvedValue([
        {
          id: "container",
          name: "AgentCore",
          absPath: "/Users/me/Documents/AgentCore",
          missing: true,
        },
      ]),
    } as unknown as typeof window.fsApi;

    expect(await resolveLocalBind("c1")).toEqual({
      kind: "live",
      rootId: "container",
      subpath: "conversations/c1",
    });
    expect(await resolveSidecarRoot("c1")).toEqual({
      rootId: "container",
      subpath: "conversations/c1",
    });
  });
});
