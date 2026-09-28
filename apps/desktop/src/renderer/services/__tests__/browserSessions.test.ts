import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    delete: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
  },
}));

vi.mock("@/services/sidecarRouting", () => ({
  getActiveSidecarTarget: vi.fn(() => null),
  resolveSidecarRoot: vi.fn(() => Promise.resolve(null)),
  resolveConversationLocalTarget: vi.fn(() => Promise.resolve(null)),
  resolveLocalBind: vi.fn(() => Promise.resolve({ kind: "unbound" as const })),
}));

import { api } from "@/services/api";
import {
  getActiveSidecarTarget,
  resolveConversationLocalTarget,
  resolveLocalBind,
  resolveSidecarRoot,
} from "@/services/sidecarRouting";
import {
  closeBrowserSession,
  createBrowserSession,
  listBrowserSessions,
  navigateBrowserSession,
} from "../browserSessions";

const getMock = vi.mocked(api.get);
const deleteMock = vi.mocked(api.delete);
const postMock = vi.mocked(api.post);
const getActiveMock = vi.mocked(getActiveSidecarTarget);
const resolveRootMock = vi.mocked(resolveSidecarRoot);
const resolveLocalMock = vi.mocked(resolveConversationLocalTarget);
const resolveBindMock = vi.mocked(resolveLocalBind);

beforeEach(() => {
  getMock.mockReset();
  deleteMock.mockReset();
  postMock.mockReset();
  getActiveMock.mockReset();
  resolveRootMock.mockReset();
  resolveLocalMock.mockReset();
  resolveBindMock.mockReset();
  getActiveMock.mockReturnValue(null);
  resolveRootMock.mockResolvedValue(null);
  resolveLocalMock.mockResolvedValue(null);
  resolveBindMock.mockResolvedValue({ kind: "unbound" });
  vi.stubGlobal("window", {});
});

describe("browserSessions service", () => {
  it("listBrowserSessions maps snake_case wire to camelCase", async () => {
    getMock.mockResolvedValue({
      data: [
        {
          session_id: "s1",
          conversation_id: "c1",
          host_kind: "sandbox",
          control: "agent",
          run_id: null,
          created_at: 10,
          last_used: 20,
        },
      ],
      active_session_id: "s1",
    });

    const result = await listBrowserSessions("c1");
    expect(getMock).toHaveBeenCalledWith(
      "/v1/conversations/c1/browser/sessions",
    );
    expect(result).toEqual({
      sessions: [
        {
          sessionId: "s1",
          conversationId: "c1",
          hostKind: "sandbox",
          control: "agent",
          runId: null,
          createdAt: 10,
          lastUsed: 20,
          url: null,
          title: null,
        },
      ],
      activeSessionId: "s1",
    });
  });

  it("listBrowserSessions routes Local → sidecar when target resolves", async () => {
    resolveRootMock.mockResolvedValue({
      rootId: "root-1",
      subpath: "conversations/c1",
    });
    const listRpc = vi.fn().mockResolvedValue({
      data: [
        {
          session_id: "local-s1",
          conversation_id: "c1",
          host_kind: "local",
          control: "agent",
          run_id: null,
          created_at: 1,
          last_used: 2,
          url: "https://local.example/",
          title: "Local",
        },
      ],
      active_session_id: "local-s1",
    });
    // @ts-expect-error partial stub
    window.sidecarApi = { listBrowserSessions: listRpc };

    const result = await listBrowserSessions("c1");
    expect(getMock).not.toHaveBeenCalled();
    expect(listRpc).toHaveBeenCalledWith({
      rootId: "root-1",
      subpath: "conversations/c1",
      conversationId: "c1",
    });
    expect(result.activeSessionId).toBe("local-s1");
    expect(result.sessions[0]).toMatchObject({
      sessionId: "local-s1",
      hostKind: "local",
      url: "https://local.example/",
    });
  });

  it("listBrowserSessions prefers active sidecar turn target over resolve", async () => {
    getActiveMock.mockReturnValue({
      rootId: "active-root",
      subpath: "",
      turnId: "t1",
    });
    const listRpc = vi.fn().mockResolvedValue({
      data: [],
      active_session_id: null,
    });
    // @ts-expect-error partial stub
    window.sidecarApi = { listBrowserSessions: listRpc };

    await listBrowserSessions("c1");
    expect(resolveRootMock).not.toHaveBeenCalled();
    expect(listRpc).toHaveBeenCalledWith({
      rootId: "active-root",
      subpath: "",
      conversationId: "c1",
    });
  });

  it("listBrowserSessions returns empty for Local-bound without sidecar (no cloud GET)", async () => {
    resolveLocalMock.mockResolvedValue({
      rootId: "root-1",
      subpath: "conversations/c1",
    });

    const result = await listBrowserSessions("c1");

    expect(getMock).not.toHaveBeenCalled();
    expect(result).toEqual({ sessions: [], activeSessionId: null });
  });

  it("listBrowserSessions returns empty for stale bind (no cloud GET)", async () => {
    resolveBindMock.mockResolvedValue({
      kind: "stale",
      rootId: "root-1",
      subpath: "",
      absPath: "/gone",
    });

    const result = await listBrowserSessions("c1");

    expect(getMock).not.toHaveBeenCalled();
    expect(result).toEqual({ sessions: [], activeSessionId: null });
  });

  it("listBrowserSessions returns empty when the root id is not on this computer", async () => {
    resolveBindMock.mockResolvedValue({
      kind: "absent",
      rootId: "root-1",
      subpath: "",
    });

    const result = await listBrowserSessions("c1");

    expect(getMock).not.toHaveBeenCalled();
    expect(result).toEqual({ sessions: [], activeSessionId: null });
  });

  it("createBrowserSession POSTs host_kind sandbox by default", async () => {
    postMock.mockResolvedValue({
      session_id: "s-new",
      conversation_id: "c1",
      host_kind: "sandbox",
      control: "agent",
      run_id: null,
      created_at: 1,
      last_used: 1,
    });
    const info = await createBrowserSession("c1", {
      hostKind: "sandbox",
      activate: true,
    });
    expect(postMock).toHaveBeenCalledWith(
      "/v1/conversations/c1/browser/sessions",
      { host_kind: "sandbox", activate: true },
    );
    expect(info.sessionId).toBe("s-new");
    expect(info.hostKind).toBe("sandbox");
  });

  it("navigateBrowserSession POSTs url to …/navigate", async () => {
    postMock.mockResolvedValue({
      session_id: "s1",
      conversation_id: "c1",
      host_kind: "sandbox",
      control: "agent",
      run_id: null,
      created_at: 1,
      last_used: 2,
      url: "https://example.com/",
    });
    const info = await navigateBrowserSession(
      "c1",
      "s1",
      "https://example.com/",
    );
    expect(postMock).toHaveBeenCalledWith(
      "/v1/conversations/c1/browser/sessions/s1/navigate",
      { url: "https://example.com/" },
    );
    expect(info.url).toBe("https://example.com/");
  });

  it("closeBrowserSession DELETEs the session path", async () => {
    deleteMock.mockResolvedValue({});
    await closeBrowserSession("c1", "s1");
    expect(deleteMock).toHaveBeenCalledWith(
      "/v1/conversations/c1/browser/sessions/s1",
    );
  });
});
