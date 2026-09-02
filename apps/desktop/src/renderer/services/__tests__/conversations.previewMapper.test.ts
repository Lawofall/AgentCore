vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { api } from "@/services/api";
import { listConversations, listGrouped } from "@/services/conversations";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.mocked(api.get);

const SERVER_PREVIEW = "服务端助手摘要，不是用户句";

function summary(preview: string | null | undefined) {
  return {
    id: "c1",
    title: "对话",
    updated_at: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    message_count: 4,
    pinned: false,
    archived: false,
    ...(preview !== undefined ? { last_message_preview: preview } : {}),
  };
}

beforeEach(() => {
  get.mockReset();
});

describe("toConversation last_message_preview", () => {
  it("maps grouped last_message_preview instead of hardcoding null", async () => {
    get.mockResolvedValue({
      folders: [],
      ungrouped: [summary(SERVER_PREVIEW)],
    });

    const { conversations } = await listGrouped();

    expect(get).toHaveBeenCalledWith("/v1/conversations/grouped");
    expect(conversations[0].lastMessagePreview).toBe(SERVER_PREVIEW);
    expect(conversations[0].lastMessagePreview).not.toBeNull();
  });

  it("still shows the server preview after a second grouped fetch (invalidate refetch)", async () => {
    get.mockResolvedValue({
      folders: [],
      ungrouped: [summary(SERVER_PREVIEW)],
    });

    await listGrouped();
    const { conversations } = await listGrouped();

    expect(conversations[0].lastMessagePreview).toBe(SERVER_PREVIEW);
  });

  it("maps list endpoint last_message_preview", async () => {
    get.mockResolvedValue({
      data: [summary(SERVER_PREVIEW)],
      page: 1,
      page_size: 100,
      total: 1,
    });

    const rows = await listConversations();
    expect(rows[0].lastMessagePreview).toBe(SERVER_PREVIEW);
  });

  it("maps missing or blank last_message_preview to null", async () => {
    get
      .mockResolvedValueOnce({
        folders: [],
        ungrouped: [summary(null)],
      })
      .mockResolvedValueOnce({
        folders: [],
        ungrouped: [summary("   ")],
      })
      .mockResolvedValueOnce({
        folders: [],
        ungrouped: [summary(undefined)],
      });

    expect(
      (await listGrouped()).conversations[0].lastMessagePreview,
    ).toBeNull();
    expect(
      (await listGrouped()).conversations[0].lastMessagePreview,
    ).toBeNull();
    expect(
      (await listGrouped()).conversations[0].lastMessagePreview,
    ).toBeNull();
  });

  it("maps compacted_through for the timeline divider, never a summary body", async () => {
    const mark = "2026-01-01T12:00:00Z";
    get.mockResolvedValue({
      folders: [],
      ungrouped: [
        {
          ...summary(SERVER_PREVIEW),
          context_compacted: true,
          compacted_through: mark,
        },
      ],
    });

    const { conversations } = await listGrouped();
    expect(conversations[0].contextCompacted).toBe(true);
    expect(conversations[0].compactedThrough).toBe(mark);
  });
});
