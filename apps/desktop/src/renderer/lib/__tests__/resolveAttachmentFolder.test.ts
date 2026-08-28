import {
  decideDraftFolderAssign,
  resolveFolderFromCitedRoot,
  resolveFolderFromIndexedEntry,
} from "@/components/chat/message-input/resolveAttachmentFolder";
import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import type { IndexedEntry } from "@/lib/fileIndex";
import type { FolderMeta } from "@/services/folders";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(),
}));

vi.mock("@/hooks/useFolders", () => ({
  getFolders: vi.fn(),
}));

const folder = (
  id: string,
  name: string,
  patch: Partial<FolderMeta> = {},
): FolderMeta => ({
  id,
  name,
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
  ...patch,
});

const entry = (patch: Partial<IndexedEntry>): IndexedEntry => ({
  sourceId: "local:root-1",
  sourceLabel: "Demo",
  relPath: "a.txt",
  name: "a.txt",
  display: "Demo/a.txt",
  kind: "file",
  ...patch,
});

describe("resolveFolderFromIndexedEntry", () => {
  beforeEach(() => {
    vi.mocked(getConversations).mockReturnValue([]);
    vi.mocked(getFolders).mockReturnValue([]);
  });

  it("maps cloud workspace source to folder id", () => {
    vi.mocked(getFolders).mockReturnValue([folder("f-1", "云项目")]);
    const result = resolveFolderFromIndexedEntry(
      entry({ sourceId: "workspace:folder:f-1", sourceLabel: "云项目" }),
    );
    expect(result).toEqual({ folderId: "f-1", folderName: "云项目" });
  });

  it("maps local root to a project sharing that root", () => {
    vi.mocked(getFolders).mockReturnValue([
      folder("f-local", "本地仓", {
        mode: "local",
        localRootId: "root-9",
      }),
    ]);
    const result = resolveFolderFromIndexedEntry(
      entry({ sourceId: "local:root-9:sub", sourceLabel: "本地仓" }),
    );
    expect(result).toEqual({ folderId: "f-local", folderName: "本地仓" });
  });

  it("maps conversation mention to its folder", () => {
    vi.mocked(getConversations).mockReturnValue([
      {
        id: "c-1",
        title: "某对话",
        folderId: "f-2",
        updatedAt: "",
        messageCount: 1,
        lastMessagePreview: null,
        localContainerRootId: null,
      },
    ]);
    vi.mocked(getFolders).mockReturnValue([folder("f-2", "项目 B")]);
    const result = resolveFolderFromIndexedEntry(
      entry({
        kind: "conversation",
        relPath: "c-1",
        name: "某对话",
        sourceId: "conversation",
      }),
    );
    expect(result).toEqual({ folderId: "f-2", folderName: "项目 B" });
  });

  it("returns null for unbound local root", () => {
    vi.mocked(getFolders).mockReturnValue([]);
    expect(
      resolveFolderFromIndexedEntry(entry({ sourceId: "local:orphan" })),
    ).toBeNull();
  });

  it("picks the longest localSubpath prefix on the same root", () => {
    vi.mocked(getFolders).mockReturnValue([
      folder("f-root", "整仓", {
        mode: "local",
        localRootId: "root-9",
        localSubpath: null,
      }),
      folder("f-docs", "文档", {
        mode: "local",
        localRootId: "root-9",
        localSubpath: "docs",
      }),
    ]);
    expect(
      resolveFolderFromCitedRoot("root-9", "docs/guide.md"),
    ).toEqual({ folderId: "f-docs", folderName: "文档" });
    expect(resolveFolderFromCitedRoot("root-9", "src/a.ts")).toEqual({
      folderId: "f-root",
      folderName: "整仓",
    });
  });
});

describe("decideDraftFolderAssign", () => {
  const hint = { folderId: "f-docs", folderName: "文档" };

  it("auto-assigns from quick cloud or leftover quick local", () => {
    expect(decideDraftFolderAssign(hint, { kind: "quick_cloud" })).toEqual({
      action: "auto",
      ...hint,
    });
    expect(decideDraftFolderAssign(hint, { kind: "quick_local" })).toEqual({
      action: "auto",
      ...hint,
    });
  });

  it("prompts when the draft already uses another folder", () => {
    expect(
      decideDraftFolderAssign(hint, { kind: "folder", folderId: "f-other" }),
    ).toEqual({ action: "prompt", ...hint });
  });

  it("is a no-op when the draft is already that folder", () => {
    expect(
      decideDraftFolderAssign(hint, { kind: "folder", folderId: "f-docs" }),
    ).toEqual({ action: "none" });
  });
});
