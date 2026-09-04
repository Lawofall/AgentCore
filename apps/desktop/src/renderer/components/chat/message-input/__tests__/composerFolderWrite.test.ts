import type { FolderMeta } from "@/services/folders";
import { describe, expect, it } from "vitest";
import { isComposerFolderWriteBlocked } from "../composerFolderWrite";

function desk(id: string, myRole: FolderMeta["myRole"]): FolderMeta {
  return {
    id,
    name: id,
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
    myRole,
  };
}

describe("isComposerFolderWriteBlocked", () => {
  it("viewer 当前对话拦；owner / editor 不拦", () => {
    const conversations = [{ id: "c1", folderId: "f1" }];
    expect(
      isComposerFolderWriteBlocked({
        conversationId: "c1",
        conversations,
        folders: [desk("f1", "viewer")],
        draftIntent: { kind: "quick_cloud" },
      }),
    ).toBe(true);
    expect(
      isComposerFolderWriteBlocked({
        conversationId: "c1",
        conversations,
        folders: [desk("f1", "owner")],
        draftIntent: { kind: "quick_cloud" },
      }),
    ).toBe(false);
    expect(
      isComposerFolderWriteBlocked({
        conversationId: "c1",
        conversations,
        folders: [desk("f1", "editor")],
        draftIntent: { kind: "quick_cloud" },
      }),
    ).toBe(false);
  });

  it("草稿 folder 意图跟 viewer；裸聊 / 找不到 folder 不拦", () => {
    expect(
      isComposerFolderWriteBlocked({
        conversationId: null,
        conversations: [],
        folders: [desk("f1", "viewer")],
        draftIntent: { kind: "folder", folderId: "f1" },
      }),
    ).toBe(true);
    expect(
      isComposerFolderWriteBlocked({
        conversationId: null,
        conversations: [],
        folders: [desk("f1", "viewer")],
        draftIntent: { kind: "quick_cloud" },
      }),
    ).toBe(false);
    expect(
      isComposerFolderWriteBlocked({
        conversationId: "c1",
        conversations: [{ id: "c1", folderId: "gone" }],
        folders: [desk("f1", "viewer")],
        draftIntent: { kind: "quick_cloud" },
      }),
    ).toBe(false);
    expect(
      isComposerFolderWriteBlocked({
        conversationId: "c1",
        conversations: [{ id: "c1", folderId: null }],
        folders: [desk("f1", "viewer")],
        draftIntent: { kind: "quick_cloud" },
      }),
    ).toBe(false);
  });
});
