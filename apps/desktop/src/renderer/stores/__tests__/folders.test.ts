import { hasLocalFiles } from "@/lib/capabilities";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defaultDraftWorkspaceIntent, useFoldersStore } from "../folders";

const { getComposerChannelPreference } = vi.hoisted(() => ({
  getComposerChannelPreference: vi.fn(() => "local_traditional"),
}));

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => false),
}));

vi.mock("@/lib/composerChannelPreference", () => ({
  getComposerChannelPreference,
}));

const store = () => useFoldersStore.getState();

beforeEach(() => {
  vi.mocked(hasLocalFiles).mockReturnValue(false);
  getComposerChannelPreference.mockReturnValue("local_traditional");
  useFoldersStore.setState({
    pendingRevealFolderId: null,
    pendingRenameFolderId: null,
    pendingUntitledCreate: null,
    untitledCreateBusy: false,
    draftWorkspaceIntent: defaultDraftWorkspaceIntent(),
    borrowToCloudOpen: false,
    borrowToCloudPrefill: null,
    connectGitOpen: false,
    connectGitWsId: null,
  });
});

describe("pending markers", () => {
  it("tracks created-folder reveal independently of draft intent", () => {
    store().revealCreatedFolder("a");
    store().setDraftWorkspaceIntent({ kind: "folder", folderId: "b" });
    expect(store().pendingRevealFolderId).toBe("a");
    expect(store().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "b",
    });

    store().clearPendingReveal();
    expect(store().pendingRevealFolderId).toBeNull();
    expect(store().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "b",
    });
  });

  it("requestUntitledCloudFolder queues one create and ignores a second click", () => {
    store().requestUntitledCloudFolder();
    expect(store().pendingUntitledCreate).toBe(true);
    expect(store().untitledCreateBusy).toBe(true);
    store().requestUntitledCloudFolder();
    expect(store().pendingUntitledCreate).toBe(true);
    store().clearUntitledCreateRequest();
    expect(store().pendingUntitledCreate).toBeNull();
    store().finishUntitledCreate();
    expect(store().untitledCreateBusy).toBe(false);
  });

  it("untitled create reveals and renames; named create only reveals", () => {
    store().revealCreatedFolder("a", { rename: true });
    expect(store().pendingRevealFolderId).toBe("a");
    expect(store().pendingRenameFolderId).toBe("a");
    store().clearPendingRename();
    expect(store().pendingRenameFolderId).toBeNull();
    expect(store().pendingRevealFolderId).toBe("a");

    store().revealCreatedFolder("b");
    expect(store().pendingRevealFolderId).toBe("b");
    expect(store().pendingRenameFolderId).toBeNull();
  });

  it("switches among quick cloud / project intents", () => {
    store().setDraftWorkspaceIntent({ kind: "quick_cloud" });
    expect(store().draftWorkspaceIntent).toEqual({ kind: "quick_cloud" });

    store().setDraftWorkspaceIntent({ kind: "folder", folderId: "f1" });
    expect(store().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "f1",
    });

    store().resetDraftWorkspaceIntent();
    expect(store().draftWorkspaceIntent).toEqual({ kind: "quick_cloud" });
  });
});

describe("defaultDraftWorkspaceIntent", () => {
  it("defaults to quick_cloud when there is no local disk", () => {
    expect(defaultDraftWorkspaceIntent()).toEqual({ kind: "quick_cloud" });
  });

  it("defaults to quick_local on desktop when channel is unset", () => {
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    expect(defaultDraftWorkspaceIntent()).toEqual({ kind: "quick_local" });
  });

  it("follows a remembered cloud channel on desktop", () => {
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    getComposerChannelPreference.mockReturnValue("cloud");
    expect(defaultDraftWorkspaceIntent()).toEqual({ kind: "quick_cloud" });
  });
});

describe("borrow / connect git dialog flags", () => {
  it("openBorrowToCloud toggles independently of connectGit", () => {
    store().openBorrowToCloud();
    expect(store().borrowToCloudOpen).toBe(true);
    expect(store().connectGitOpen).toBe(false);
    store().closeBorrowToCloud();
    expect(store().borrowToCloudOpen).toBe(false);

    store().openConnectGit("folder:x");
    expect(store().connectGitOpen).toBe(true);
    expect(store().connectGitWsId).toBe("folder:x");
    store().closeConnectGit();
    expect(store().connectGitOpen).toBe(false);
    expect(store().connectGitWsId).toBeNull();
  });

  it("openBorrowToCloud accepts path prefill and clears on close", () => {
    store().openBorrowToCloud({
      rootId: "root-1",
      folderName: "MyRepo",
      ownsRoot: true,
    });
    expect(store().borrowToCloudOpen).toBe(true);
    expect(store().borrowToCloudPrefill).toEqual({
      rootId: "root-1",
      folderName: "MyRepo",
      ownsRoot: true,
    });
    store().closeBorrowToCloud();
    expect(store().borrowToCloudOpen).toBe(false);
    expect(store().borrowToCloudPrefill).toBeNull();
  });
});
