import {
  MAX_WORKSPACE_GROUPS,
  buildWorkspaceGroups,
} from "@/hooks/useWorkspaceGroups";
import type { FolderMeta } from "@/services/folders";
import type { Conversation } from "@/stores/conversation";
import { describe, expect, it } from "vitest";

const folder = (id: string, name = id): FolderMeta => ({
  id,
  name,
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
});

const conv = (
  id: string,
  opts: { folderId?: string | null; at?: string; pinned?: boolean } = {},
): Conversation => ({
  id,
  title: id,
  updatedAt: opts.at ?? "2026-01-01T00:00:00Z",
  messageCount: 0,
  lastMessagePreview: null,
  folderId: opts.folderId ?? null,
  pinned: opts.pinned,
});

describe("buildWorkspaceGroups (方案B 项目分组)", () => {
  it("groups foldered chats by folder and excludes 裸聊", () => {
    const groups = buildWorkspaceGroups(
      [
        conv("a", { folderId: "f1" }),
        conv("bare", { folderId: null }),
        conv("b", { folderId: "f1" }),
      ],
      [folder("f1")],
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].folder.id).toBe("f1");
    expect(groups[0].convs.map((c) => c.id).sort()).toEqual(["a", "b"]);
  });

  it("skips conversations whose folder is not in cache", () => {
    const groups = buildWorkspaceGroups(
      [conv("a", { folderId: "ghost" })],
      [folder("f1")],
    );
    expect(groups).toHaveLength(0);
  });

  it("orders groups by latest activity (newest folder first)", () => {
    const groups = buildWorkspaceGroups(
      [
        conv("old", { folderId: "f1", at: "2026-01-01T00:00:00Z" }),
        conv("new", { folderId: "f2", at: "2026-02-01T00:00:00Z" }),
      ],
      [folder("f1"), folder("f2")],
    );
    expect(groups.map((g) => g.folder.id)).toEqual(["f2", "f1"]);
  });

  it("sorts within a group newest-first (pinned stay in members for header)", () => {
    const groups = buildWorkspaceGroups(
      [
        conv("oldPinned", {
          folderId: "f1",
          at: "2026-01-01T00:00:00Z",
          pinned: true,
        }),
        conv("newer", { folderId: "f1", at: "2026-03-01T00:00:00Z" }),
        conv("newest", { folderId: "f1", at: "2026-04-01T00:00:00Z" }),
      ],
      [folder("f1")],
    );
    expect(groups[0].convs.map((c) => c.id)).toEqual([
      "newest",
      "newer",
      "oldPinned",
    ]);
  });

  it("required 所在组挤进 ≤6，不另开栏", () => {
    const folders = Array.from({ length: MAX_WORKSPACE_GROUPS + 1 }, (_, i) =>
      folder(`f${i}`),
    );
    const conversations = folders.map((f, i) =>
      conv(`c${i}`, {
        folderId: f.id,
        at: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      }),
    );
    const groups = buildWorkspaceGroups(
      conversations,
      folders,
      new Set(["c0"]),
    );
    expect(groups).toHaveLength(MAX_WORKSPACE_GROUPS);
    expect(groups.some((g) => g.folder.id === "f0")).toBe(true);
    expect(groups.some((g) => g.folder.id === "f1")).toBe(false);
  });

  it("置顶 required 不为此挤进第 7 组（行已在置顶区）", () => {
    const folders = Array.from({ length: MAX_WORKSPACE_GROUPS + 1 }, (_, i) =>
      folder(`f${i}`),
    );
    const conversations = folders.map((f, i) =>
      conv(`c${i}`, {
        folderId: f.id,
        at: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
        pinned: i === 0,
      }),
    );
    const groups = buildWorkspaceGroups(
      conversations,
      folders,
      new Set(["c0"]),
    );
    expect(groups).toHaveLength(MAX_WORKSPACE_GROUPS);
    expect(groups.some((g) => g.folder.id === "f0")).toBe(false);
  });

  it("caps the number of groups at MAX_WORKSPACE_GROUPS", () => {
    const folders = Array.from({ length: MAX_WORKSPACE_GROUPS + 3 }, (_, i) =>
      folder(`f${i}`),
    );
    const conversations = folders.map((f, i) =>
      conv(`c${i}`, {
        folderId: f.id,
        // strictly increasing activity so ordering is deterministic
        at: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      }),
    );
    const groups = buildWorkspaceGroups(conversations, folders);
    expect(groups).toHaveLength(MAX_WORKSPACE_GROUPS);
    // the most recent folders survive the cap
    expect(groups[0].folder.id).toBe(`f${folders.length - 1}`);
  });

  it("uncapped keeps every folder group (narrow conversation drawer)", () => {
    const folders = Array.from({ length: MAX_WORKSPACE_GROUPS + 3 }, (_, i) =>
      folder(`f${i}`),
    );
    const conversations = folders.map((f, i) =>
      conv(`c${i}`, { folderId: f.id }),
    );
    const groups = buildWorkspaceGroups(conversations, folders, new Set(), {
      uncapped: true,
    });
    expect(groups).toHaveLength(folders.length);
  });

  it("merges conversations under duplicate local bindings into the oldest folder", () => {
    const folders: FolderMeta[] = [
      {
        id: "oldest",
        name: "Oldest",
        mode: "local",
        localRootId: "root-1",
        localSubpath: null,
      },
      {
        id: "dup",
        name: "Dup",
        mode: "local",
        localRootId: "root-1",
        localSubpath: null,
      },
    ];
    const groups = buildWorkspaceGroups(
      [
        conv("a", { folderId: "oldest", at: "2026-01-01T00:00:00Z" }),
        conv("b", { folderId: "dup", at: "2026-02-01T00:00:00Z" }),
      ],
      folders,
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].folder.id).toBe("oldest");
    expect(groups[0].convs.map((c) => c.id)).toEqual(["b", "a"]);
  });

  it("empty folderGroupOrder keeps activity order", () => {
    const groups = buildWorkspaceGroups(
      [
        conv("old", { folderId: "f1", at: "2026-01-01T00:00:00Z" }),
        conv("new", { folderId: "f2", at: "2026-02-01T00:00:00Z" }),
      ],
      [folder("f1"), folder("f2")],
      new Set(),
      { folderGroupOrder: [] },
    );
    expect(groups.map((g) => g.folder.id)).toEqual(["f2", "f1"]);
  });

  it("pinned order is sticky: a hotter group cannot jump a later pin", () => {
    const groups = buildWorkspaceGroups(
      [
        conv("old", { folderId: "f1", at: "2026-01-01T00:00:00Z" }),
        conv("new", { folderId: "f2", at: "2026-02-01T00:00:00Z" }),
      ],
      [folder("f1"), folder("f2")],
      new Set(),
      { folderGroupOrder: ["f1", "f2"] },
    );
    expect(groups.map((g) => g.folder.id)).toEqual(["f1", "f2"]);
  });

  it("unknown ids follow stored, sorted by activity among themselves", () => {
    const groups = buildWorkspaceGroups(
      [
        conv("slow", { folderId: "pinned", at: "2026-01-01T00:00:00Z" }),
        conv("hot", { folderId: "new-hot", at: "2026-03-01T00:00:00Z" }),
        conv("warm", { folderId: "new-warm", at: "2026-02-01T00:00:00Z" }),
      ],
      [folder("pinned"), folder("new-hot"), folder("new-warm")],
      new Set(),
      { folderGroupOrder: ["pinned"] },
    );
    expect(groups.map((g) => g.folder.id)).toEqual([
      "pinned",
      "new-hot",
      "new-warm",
    ]);
  });

  it("cap follows pinned order, not activity", () => {
    const folders = Array.from({ length: MAX_WORKSPACE_GROUPS + 1 }, (_, i) =>
      folder(`f${i}`),
    );
    const conversations = folders.map((f, i) =>
      conv(`c${i}`, {
        folderId: f.id,
        at: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      }),
    );
    // Pin oldest-first so the hottest group is last and dropped by the cap.
    const folderGroupOrder = folders.map((f) => f.id);
    const groups = buildWorkspaceGroups(conversations, folders, new Set(), {
      folderGroupOrder,
    });
    expect(groups).toHaveLength(MAX_WORKSPACE_GROUPS);
    expect(groups.map((g) => g.folder.id)).toEqual(
      folderGroupOrder.slice(0, MAX_WORKSPACE_GROUPS),
    );
  });

  it("required squeeze-in still overlays a pinned order without sorting by activity", () => {
    const folders = Array.from({ length: MAX_WORKSPACE_GROUPS + 1 }, (_, i) =>
      folder(`f${i}`),
    );
    const conversations = folders.map((f, i) =>
      conv(`c${i}`, {
        folderId: f.id,
        at: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      }),
    );
    const folderGroupOrder = folders.map((f) => f.id);
    const groups = buildWorkspaceGroups(
      conversations,
      folders,
      new Set([`c${MAX_WORKSPACE_GROUPS}`]),
      { folderGroupOrder },
    );
    expect(groups).toHaveLength(MAX_WORKSPACE_GROUPS);
    expect(groups.map((g) => g.folder.id)).toEqual([
      ...folderGroupOrder.slice(0, MAX_WORKSPACE_GROUPS - 1),
      folderGroupOrder[MAX_WORKSPACE_GROUPS],
    ]);
  });
});
