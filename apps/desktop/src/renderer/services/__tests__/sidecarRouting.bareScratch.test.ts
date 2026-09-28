// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(),
}));
vi.mock("@/hooks/useFolders", () => ({
  getFolders: vi.fn(),
}));
vi.mock("@/lib/queryClient", () => ({
  queryClient: { getQueryData: vi.fn() },
}));
vi.mock("@/lib/queryKeys", () => ({
  workspaceKeys: { list: ["workspaces"] },
}));
vi.mock("@/lib/capabilities", () => ({
  hasLocalEngine: () => true,
}));
vi.mock("@/stores/conversation", () => ({
  getRuntime: () => ({ messages: [] }),
}));

import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { queryClient } from "@/lib/queryClient";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";

const getConvs = getConversations as unknown as ReturnType<typeof vi.fn>;
const getFolds = getFolders as unknown as ReturnType<typeof vi.fn>;
const getQueryData = queryClient.getQueryData as unknown as ReturnType<
  typeof vi.fn
>;

describe("resolveConversationLocalTarget (裸聊隔离)", () => {
  beforeEach(() => {
    getConvs.mockReset();
    getFolds.mockReset();
    getQueryData.mockReset();
    getFolds.mockReturnValue([]);
    getQueryData.mockReturnValue(undefined);
    window.fsApi = {
      listRoots: vi
        .fn()
        .mockResolvedValue([{ id: "container", name: "AgentCore" }]),
    } as unknown as typeof window.fsApi;
  });

  it("maps bare chat on container root to conversations/<id>", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: null,
        localContainerRootId: "container",
      },
    ]);
    const target = await resolveConversationLocalTarget("c1");
    expect(target).toEqual({
      rootId: "container",
      subpath: "conversations/c1",
    });
  });

  it("prefers a non-empty workspace-cache subpath", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: null,
        localContainerRootId: "container",
      },
    ]);
    getQueryData.mockReturnValue([
      {
        wsId: "conv:c1",
        rootId: "container",
        subpath: "conversations/c1",
        name: "t",
        location: "local",
        hasFiles: true,
      },
    ]);
    const target = await resolveConversationLocalTarget("c1");
    expect(target?.subpath).toBe("conversations/c1");
  });

  it("maps empty subpath to conversations/<id> even on a non-container root", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: null,
        localContainerRootId: "container",
      },
    ]);
    getQueryData.mockReturnValue([
      {
        wsId: "conv:c1",
        rootId: "other-root",
        subpath: "",
        name: "t",
        location: "local",
        hasFiles: true,
      },
    ]);
    (window.fsApi.listRoots as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: "container", name: "AgentCore" },
      { id: "other-root", name: "Proj" },
    ]);
    const target = await resolveConversationLocalTarget("c1");
    expect(target).toEqual({
      rootId: "other-root",
      subpath: "conversations/c1",
    });
  });

  it("resolves from localRootId alone when workspace cache is empty", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: null,
        localContainerRootId: null,
        localRootId: "bound-root",
      },
    ]);
    getQueryData.mockReturnValue(undefined);
    (window.fsApi.listRoots as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: "bound-root", name: "MyFolder" },
    ]);
    const target = await resolveConversationLocalTarget("c1");
    expect(target).toEqual({
      rootId: "bound-root",
      subpath: "conversations/c1",
    });
  });

  it("inherits folder local root + subpath for project chats", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: "f1",
        localContainerRootId: "container",
      },
    ]);
    getFolds.mockReturnValue([
      {
        id: "f1",
        name: "Proj",
        mode: "local",
        localRootId: "proj-root",
        localSubpath: "apps",
      },
    ]);
    (window.fsApi.listRoots as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: "proj-root", name: "Proj" },
    ]);
    const target = await resolveConversationLocalTarget("c1");
    expect(target).toEqual({ rootId: "proj-root", subpath: "apps" });
  });

  it("returns null for cloud bare chat (no container root)", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: null,
        localContainerRootId: null,
      },
    ]);
    const target = await resolveConversationLocalTarget("c1");
    expect(target).toBeNull();
  });

  it("§7.2 mode=cloud folder → null（无 sidecar / 无本机 target）", async () => {
    getConvs.mockReturnValue([
      {
        id: "c1",
        title: "t",
        folderId: "f-cloud",
        localContainerRootId: "container",
      },
    ]);
    getFolds.mockReturnValue([
      {
        id: "f-cloud",
        name: "Cloud",
        mode: "cloud",
        localRootId: null,
        localSubpath: null,
      },
    ]);
    const target = await resolveConversationLocalTarget("c1");
    expect(target).toBeNull();
  });
});
