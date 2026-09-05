import { getConversations } from "@/hooks/useConversations";
import {
  addFolderCache,
  patchFolderCache,
  releaseFolderConversations,
  removeFolderFromCache,
} from "@/hooks/useFolders";
import { queryClient } from "@/lib/queryClient";
import { conversationKeys } from "@/lib/queryKeys";
import type { FolderMeta } from "@/services/folders";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mk = (id: string, name = id): FolderMeta => ({
  id,
  name,
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
});

function readFolders(): FolderMeta[] {
  return (
    queryClient.getQueryData<{ folders: FolderMeta[] }>(
      conversationKeys.grouped,
    )?.folders ?? []
  );
}

function seed(folders: FolderMeta[], conversations: unknown[] = []): void {
  queryClient.setQueryData(conversationKeys.grouped, {
    folders,
    conversations,
  });
}

beforeEach(() => {
  queryClient.clear();
});

// The folder list shares the /grouped cache entry with conversations. These
// helpers (used by the folder mutations + the workspace-bind mirror) must touch
// only the folders half, leaving the conversations half intact.
describe("folder list cache helpers", () => {
  it("addFolderCache prepends onto a cold cache", () => {
    addFolderCache(mk("a"));
    expect(readFolders().map((f) => f.id)).toEqual(["a"]);
  });

  it("addFolderCache prepends newest-first and dedupes by id", () => {
    seed([mk("a"), mk("b")]);
    addFolderCache(mk("c"));
    expect(readFolders().map((f) => f.id)).toEqual(["c", "a", "b"]);

    addFolderCache(mk("b", "renamed"));
    expect(readFolders().map((f) => f.id)).toEqual(["b", "c", "a"]);
    expect(readFolders()[0].name).toBe("renamed");
  });

  it("patchFolderCache shallow-merges one folder", () => {
    seed([mk("a", "Work"), mk("b", "Notes")]);
    patchFolderCache("a", { name: "Renamed" });
    expect(readFolders().find((f) => f.id === "a")?.name).toBe("Renamed");
    expect(readFolders().find((f) => f.id === "b")?.name).toBe("Notes");
  });

  it("patchFolderCache is a no-op for an unknown id", () => {
    seed([mk("a", "Work")]);
    patchFolderCache("missing", { name: "x" });
    expect(readFolders()[0].name).toBe("Work");
  });

  it("removeFolderFromCache drops the matching folder", () => {
    seed([mk("a"), mk("b")]);
    removeFolderFromCache("a");
    expect(readFolders().map((f) => f.id)).toEqual(["b"]);
  });

  it("preserves the conversations half of the shared cache entry", () => {
    seed([mk("a")], [{ id: "c1" }]);
    addFolderCache(mk("b"));
    patchFolderCache("a", { name: "X" });
    removeFolderFromCache("b");
    const data = queryClient.getQueryData<{
      folders: FolderMeta[];
      conversations: { id: string }[];
    }>(conversationKeys.grouped);
    expect(data?.conversations).toEqual([{ id: "c1" }]);
    expect(data?.folders.map((f) => f.id)).toEqual(["a"]);
  });
});

// Deleting a project archives its conversations server-side in one statement.
// The renderer must NOT replay that through the archive endpoint (that would
// stamp them 用户主动归档 and break restore) — it only unloads them locally.
describe("releaseFolderConversations", () => {
  const conv = (id: string, folderId: string | null) => ({
    id,
    folderId,
    title: id,
  });

  it("drops only the folder's conversations and reports the active one", () => {
    seed(
      [mk("f1"), mk("f2")],
      [conv("c1", "f1"), conv("c2", "f2"), conv("c3", "f1"), conv("c4", null)],
    );
    const dropRuntime = vi.fn();

    const leftActive = releaseFolderConversations("f1", {
      dropRuntime,
      locationId: "c3",
    });

    expect(leftActive).toBe(true);
    expect(dropRuntime.mock.calls.map(([id]) => id)).toEqual(["c1", "c3"]);
    expect(getConversations().map((c) => c.id)).toEqual(["c2", "c4"]);
  });

  it("reports false when the open conversation lives elsewhere", () => {
    seed([mk("f1")], [conv("c1", "f1"), conv("c2", null)]);

    expect(
      releaseFolderConversations("f1", {
        dropRuntime: vi.fn(),
        locationId: "c2",
      }),
    ).toBe(false);
    expect(getConversations().map((c) => c.id)).toEqual(["c2"]);
  });
});
