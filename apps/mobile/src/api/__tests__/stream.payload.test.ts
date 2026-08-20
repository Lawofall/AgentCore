import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  apiUrl: (path: string) => `http://test${path}`,
  authHeader: () => ({ Authorization: "Bearer t" }),
  fetchWithAuthRefresh: async (doFetch: () => Promise<Response>) => doFetch(),
}));
vi.mock("@/lib/clientBuildInfo", () => ({
  clientHeaders: () => ({}),
}));

import { continueStream, streamMessage } from "../stream";

describe("streamMessage payload", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      new Response(new ReadableStream({ start: (c) => c.close() }), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("includes agent_mentions and conversation attachments when present", async () => {
    await streamMessage(
      "c1",
      "看这个",
      () => {},
      undefined,
      [
        {
          name: "上周复盘",
          path: "对话",
          text: "用户: 问",
          truncated: false,
          kind: "conversation",
          conversation_id: "c2",
        },
      ],
      "steer",
      [{ agent_id: "w1", role: "研究员" }],
    );
    const body = JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string) as {
      agent_mentions?: unknown;
      attachments?: { kind?: string; conversation_id?: string }[];
    };
    expect(body.agent_mentions).toEqual([{ agent_id: "w1", role: "研究员" }]);
    expect(body.attachments?.[0]?.kind).toBe("conversation");
    expect(body.attachments?.[0]?.conversation_id).toBe("c2");
  });

  it("POSTs continue with an empty body", async () => {
    await continueStream("c1", "m1", () => {});
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://test/v1/conversations/c1/messages/m1/continue",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("omits agent_mentions when empty so a plain turn keeps the prior shape", async () => {
    await streamMessage("c1", "hi", () => {}, undefined, undefined, "steer");
    const body = JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string) as {
      agent_mentions?: unknown;
      attachments?: unknown;
    };
    expect(body).toEqual({ content: "hi", delivery: "steer" });
    expect(body.agent_mentions).toBeUndefined();
  });
});
