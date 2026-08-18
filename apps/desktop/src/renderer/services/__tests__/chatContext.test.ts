import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  api: { post: vi.fn() },
}));

import { api } from "@/services/api";
import {
  CHAT_CONTEXT_UNAVAILABLE_MESSAGE,
  fetchChatContext,
} from "@/services/chatContext";

const post = vi.mocked(api.post);

afterEach(() => {
  post.mockReset();
});

describe("fetchChatContext", () => {
  it("posts conversation_id and keeps user/assistant rows", async () => {
    post.mockResolvedValueOnce({
      history: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "ok" },
        { role: "system", content: "no" },
      ],
    });
    await expect(fetchChatContext("c1")).resolves.toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "ok" },
    ]);
    expect(post).toHaveBeenCalledWith(
      "/v1/account/conversations/chat-context",
      {
        conversation_id: "c1",
      },
    );
  });

  it("keeps a confirmed empty window", async () => {
    post.mockResolvedValueOnce({ history: [] });
    await expect(fetchChatContext("c1")).resolves.toEqual([]);
  });

  it("throws when the body has no history array", async () => {
    post.mockResolvedValueOnce({});
    await expect(fetchChatContext("c1")).rejects.toThrow(
      CHAT_CONTEXT_UNAVAILABLE_MESSAGE,
    );
  });
});
