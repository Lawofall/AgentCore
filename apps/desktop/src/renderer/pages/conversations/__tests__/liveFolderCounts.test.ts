import type { FolderMeta } from "@/services/folders";
import { describe, expect, it } from "vitest";
import { ALL_KEY, ARCHIVED_KEY } from "../constants";
import {
  folderHitHasNoLiveChat,
  foldersWithLiveChats,
  isUngroupedLiveChat,
  liveChatInFolder,
  partitionLiveConversationCounts,
  resolveFolderFilterSelection,
} from "../liveFolderCounts";

const cloud = (id: string, name = id): FolderMeta => ({
  id,
  name,
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
});

const local = (id: string): FolderMeta => ({
  id,
  name: id,
  mode: "local",
  localRootId: "root-1",
  localSubpath: null,
});

describe("partitionLiveConversationCounts", () => {
  it("counts pinned chats and leaves bare chats ungrouped", () => {
    const folders = [cloud("f1", "产品")];
    const counts = partitionLiveConversationCounts(
      [
        { folderId: "f1" },
        { folderId: "f1" },
        { folderId: null },
        { folderId: "missing" },
      ],
      folders,
    );
    expect(counts.perFolder.get("f1")).toBe(2);
    expect(counts.ungrouped).toBe(2);
    expect(isUngroupedLiveChat(null, counts.canonical)).toBe(true);
    expect(isUngroupedLiveChat("missing", counts.canonical)).toBe(true);
  });

  it("rolls historical local bindings onto the oldest displayed row", () => {
    const folders = [local("oldest"), local("dup")];
    const counts = partitionLiveConversationCounts(
      [{ folderId: "oldest" }, { folderId: "dup" }],
      folders,
    );
    expect(counts.perFolder.get("oldest")).toBe(2);
    expect(counts.perFolder.get("dup")).toBeUndefined();
    expect(liveChatInFolder("dup", "oldest", counts.canonical)).toBe(true);
    expect(liveChatInFolder("oldest", "dup", counts.canonical)).toBe(true);
  });
});

describe("foldersWithLiveChats", () => {
  it("drops zero badges and keeps catalog order", () => {
    const folders = [
      cloud("empty-a", "甲"),
      cloud("used", "乙"),
      cloud("empty-b", "丙"),
      cloud("also", "丁"),
    ];
    const perFolder = new Map([
      ["used", 2],
      ["also", 1],
    ]);
    expect(foldersWithLiveChats(folders, perFolder).map((f) => f.id)).toEqual([
      "used",
      "also",
    ]);
  });
});

describe("resolveFolderFilterSelection", () => {
  const folderIds = new Set(["oldest", "dup", "empty"]);

  it("leaves view filters alone", () => {
    expect(
      resolveFolderFilterSelection(ALL_KEY, folderIds, new Map(), new Map()),
    ).toBeNull();
    expect(
      resolveFolderFilterSelection(
        ARCHIVED_KEY,
        folderIds,
        new Map(),
        new Map(),
      ),
    ).toBeNull();
  });

  it("returns to 全部对话 when the selected folder has no live chats", () => {
    expect(
      resolveFolderFilterSelection(
        "empty",
        folderIds,
        new Map([["used", 1]]),
        new Map([
          ["empty", "empty"],
          ["used", "used"],
        ]),
      ),
    ).toBe(ALL_KEY);
  });

  it("moves a duplicate-binding selection onto the displayed row", () => {
    const canonical = new Map([
      ["oldest", "oldest"],
      ["dup", "oldest"],
    ]);
    expect(
      resolveFolderFilterSelection(
        "dup",
        folderIds,
        new Map([["oldest", 2]]),
        canonical,
      ),
    ).toBe("oldest");
  });
});

describe("folderHitHasNoLiveChat", () => {
  const folders = [cloud("f1", "产品")];

  it("does not treat an unloaded catalog as empty", () => {
    expect(folderHitHasNoLiveChat("f1", [], folders, false)).toBe(false);
  });

  it("sends a settled folder with no live chats to files", () => {
    expect(folderHitHasNoLiveChat("f1", [], folders, true)).toBe(true);
    expect(
      folderHitHasNoLiveChat("f1", [{ folderId: "f1" }], folders, true),
    ).toBe(false);
  });
});
