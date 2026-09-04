vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

import { api } from "@/services/api";
import { listFolderTrash, restoreFolder } from "@/services/folders";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe("最近删除 (folder trash)", () => {
  it("maps the trash payload and keeps the server's retention window", async () => {
    get.mockResolvedValue({
      data: [
        {
          id: "f1",
          name: "商标案",
          mode: "cloud",
          local_root_id: null,
          local_subpath: null,
          created_at: "2026-07-01T00:00:00Z",
          deleted_at: "2026-08-10T09:00:00Z",
          purge_at: "2026-09-09T09:00:00Z",
        },
      ],
      total: 1,
      retention_days: 30,
    });

    const trash = await listFolderTrash();

    expect(get).toHaveBeenCalledWith("/v1/folders/trash");
    expect(trash.retentionDays).toBe(30);
    expect(trash.items).toEqual([
      {
        id: "f1",
        name: "商标案",
        mode: "cloud",
        deletedAt: "2026-08-10T09:00:00Z",
        purgeAt: "2026-09-09T09:00:00Z",
      },
    ]);
  });

  it("restore returns the folder the server actually revived", async () => {
    // A live sibling took the name meanwhile, so it comes back as「名字 (2)」.
    post.mockResolvedValue({
      id: "f1",
      name: "商标案 (2)",
      mode: "local",
      local_root_id: "root-1",
      local_subpath: "cases",
    });

    const folder = await restoreFolder("f1");

    expect(post).toHaveBeenCalledWith("/v1/folders/trash/f1/restore");
    expect(folder).toEqual({
      id: "f1",
      name: "商标案 (2)",
      mode: "local",
      localRootId: "root-1",
      localSubpath: "cases",
      relPath: null,
      parentRelPath: null,
      myRole: null,
      myState: null,
      ownerUserId: null,
      collaboratorCount: 0,
    });
  });
});
