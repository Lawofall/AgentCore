vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { api } from "@/services/api";
import {
  listConversationTrash,
  purgeTrashedConversation,
  restoreConversation,
} from "@/services/conversations";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);
const del = vi.mocked(api.delete);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  del.mockReset();
});

describe("最近删除 (conversation trash)", () => {
  it("maps the trash payload and keeps the server's retention arithmetic", async () => {
    get.mockResolvedValue({
      data: [
        {
          id: "c1",
          title: "定价讨论",
          folder_id: "f1",
          message_count: 24,
          created_at: "2026-07-01T00:00:00Z",
          deleted_at: "2026-08-10T09:00:00Z",
          purge_at: "2026-09-09T09:00:00Z",
        },
      ],
      total: 1,
      retention_days: 30,
    });

    const trash = await listConversationTrash();

    expect(get).toHaveBeenCalledWith("/v1/conversations/trash");
    expect(trash.retentionDays).toBe(30);
    // purge_at is taken as given: re-deriving it client-side from deleted_at plus a
    // hard-coded window is how the countdown drifts from what the sweeper will do.
    expect(trash.items).toEqual([
      {
        id: "c1",
        title: "定价讨论",
        folderId: "f1",
        messageCount: 24,
        deletedAt: "2026-08-10T09:00:00Z",
        purgeAt: "2026-09-09T09:00:00Z",
      },
    ]);
  });

  it("gives an untitled chat the same placeholder the live list uses", async () => {
    get.mockResolvedValue({
      data: [
        {
          id: "c2",
          title: "   ",
          folder_id: null,
          message_count: 0,
          created_at: "2026-08-01T00:00:00Z",
          deleted_at: "2026-08-12T09:00:00Z",
          purge_at: "2026-09-11T09:00:00Z",
        },
      ],
      total: 1,
      retention_days: 30,
    });

    const trash = await listConversationTrash();

    expect(trash.items[0].title).toBe("新对话");
    expect(trash.items[0].folderId).toBeNull();
  });

  it("restore returns the chat still in its original project and pin state", async () => {
    post.mockResolvedValue({
      id: "c1",
      title: "定价讨论",
      folder_id: "f1",
      pinned: true,
      archived: false,
      message_count: 24,
      created_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-08-09T12:00:00Z",
    });

    const conv = await restoreConversation("c1");

    expect(post).toHaveBeenCalledWith("/v1/conversations/trash/c1/restore");
    expect(conv.folderId).toBe("f1");
    expect(conv.pinned).toBe(true);
    // The pre-delete activity time survives, which is what puts the chat back in its
    // recency group instead of at the top under「今天」.
    expect(conv.updatedAt).toBe("2026-08-09T12:00:00Z");
  });

  it("purge hits the trash item", async () => {
    del.mockResolvedValue(undefined);
    await purgeTrashedConversation("c1");
    expect(del).toHaveBeenCalledWith("/v1/conversations/trash/c1");
  });
});
