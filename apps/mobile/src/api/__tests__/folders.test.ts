import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("@/api/client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import { listCloudFolders, listFolders } from "../folders";

describe("folders API", () => {
  beforeEach(() => {
    apiFetch.mockReset();
  });

  it("lists folders and keeps only cloud ones for the mobile picker", async () => {
    apiFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "c1", name: "云桌", mode: "cloud" },
        { id: "l1", name: "本机仓", mode: "local", local_root_id: "r" },
      ],
    });
    await expect(listFolders()).resolves.toHaveLength(2);
    await expect(listCloudFolders()).resolves.toEqual([
      expect.objectContaining({ id: "c1", mode: "cloud" }),
    ]);
    expect(apiFetch).toHaveBeenCalledWith("/v1/folders");
  });
});
