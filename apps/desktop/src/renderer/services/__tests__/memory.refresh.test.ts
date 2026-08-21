import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  api: {
    put: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock("@/services/refreshAccountRulesMemory", () => ({
  scheduleAccountRulesMemoryRefresh: vi.fn(),
}));

import { api } from "@/services/api";
import {
  disputeMemoryLine,
  moveMemoryBullet,
  restoreMemoryLine,
  writeMemoryTopic,
} from "@/services/memory";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("memory writes refresh live sidecar snapshot", () => {
  it("writeMemoryTopic success schedules refresh", async () => {
    vi.mocked(api.put).mockResolvedValueOnce({
      ok: true,
      conflict: false,
      version: "v2",
    });
    await writeMemoryTopic("笔记", "body", "v1");
    expect(scheduleAccountRulesMemoryRefresh).toHaveBeenCalledTimes(1);
  });

  it("writeMemoryTopic conflict does not refresh", async () => {
    vi.mocked(api.put).mockResolvedValueOnce({
      ok: false,
      conflict: true,
      version: "v9",
    });
    await writeMemoryTopic("笔记", "body", "v1");
    expect(scheduleAccountRulesMemoryRefresh).not.toHaveBeenCalled();
  });

  it("moveMemoryBullet / dispute / restore success schedule refresh", async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        ok: true,
        conflict: false,
        source_version: "s",
        target_version: "t",
      })
      .mockResolvedValueOnce({
        ok: true,
        conflict: false,
        version: "v3",
        line_id: "ln1",
      })
      .mockResolvedValueOnce({
        ok: true,
        conflict: false,
        version: "v4",
      });
    await moveMemoryBullet({
      content: "一行",
      section: "事实",
      folderId: "F1",
      direction: "to_project",
    });
    await disputeMemoryLine({
      content: "一行",
      section: "事实",
      folderId: "F1",
    });
    await restoreMemoryLine({ id: "ln1" });
    expect(scheduleAccountRulesMemoryRefresh).toHaveBeenCalledTimes(3);
  });
});
