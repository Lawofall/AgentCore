import {
  ARCHIVED_KEY,
  TRASH_KEY,
  activeFilterName,
  emptyTrashConfirmCopy,
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

describe("emptyTrashConfirmCopy", () => {
  const listed = { listedConversations: 2, listedFolders: 1 };

  it("splits conversation and folder counts, and says the computer stays", () => {
    expect(
      emptyTrashConfirmCopy({
        conversations: 2,
        folders: 1,
        searching: false,
        ...listed,
      }),
    ).toBe(
      "将永久删除 2 条对话，以及 1 个文件夹（含其中的对话、云端文件和这张桌的设定），不可恢复。电脑上的文件夹不会被删除。",
    );
  });

  it("names only the half that is actually in the bin", () => {
    expect(
      emptyTrashConfirmCopy({
        conversations: 4,
        folders: 0,
        searching: false,
        listedConversations: 4,
        listedFolders: 0,
      }),
    ).toBe("将永久删除 4 条对话和全部消息，不可恢复。");
    expect(
      emptyTrashConfirmCopy({
        conversations: 0,
        folders: 1,
        searching: false,
        listedConversations: 0,
        listedFolders: 1,
      }),
    ).toContain("电脑上的文件夹不会被删除。");
  });

  it("says search does not narrow the wipe, and the page cap does not either", () => {
    const copy = emptyTrashConfirmCopy({
      conversations: 250,
      folders: 0,
      searching: true,
      listedConversations: 200,
      listedFolders: 0,
    });
    expect(copy).toContain("250 条对话");
    expect(copy).toContain("列表没有列全，清空仍按这个条数。");
    expect(copy).toContain("当前搜索不会缩小清空范围。");
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
