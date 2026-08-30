import {
  ARCHIVED_KEY,
  TRASH_KEY,
  activeFilterName,
  isRealFolderFilter,
  retentionRemainingLabel,
} from "@/pages/conversations/constants";
import { UNGROUPED_KEY } from "@/stores/folders";
import { describe, expect, it } from "vitest";

const NOW = Date.parse("2026-08-13T00:00:00Z");
const at = (hoursFromNow: number) =>
  new Date(NOW + hoursFromNow * 3_600_000).toISOString();

describe("retentionRemainingLabel", () => {
  it("floors the remaining days — purge_at is the earliest sweep, not a promise", () => {
    expect(retentionRemainingLabel(at(24 * 30), NOW)).toBe("剩 30 天");
    expect(retentionRemainingLabel(at(24 * 2 + 23), NOW)).toBe("剩 2 天");
  });

  it("calls out the last day and an already-due purge", () => {
    expect(retentionRemainingLabel(at(5), NOW)).toBe("剩不到 1 天");
    expect(retentionRemainingLabel(at(-1), NOW)).toBe("即将清理");
  });

  it("stays quiet on an unparseable timestamp", () => {
    expect(retentionRemainingLabel("not-a-date", NOW)).toBe("");
  });
});

describe("最近删除 filter key", () => {
  it("names the view and is never mistaken for a folder id", () => {
    expect(activeFilterName(TRASH_KEY, [])).toBe("最近删除");
    expect(activeFilterName(ARCHIVED_KEY, [])).toBe("已归档");
    expect(activeFilterName(UNGROUPED_KEY, [])).toBe("快速对话");
    expect(isRealFolderFilter(TRASH_KEY, new Set([TRASH_KEY]))).toBe(false);
    expect(isRealFolderFilter("f1", new Set(["f1"]))).toBe(true);
  });
});
