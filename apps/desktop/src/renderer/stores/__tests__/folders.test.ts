import { beforeEach, describe, expect, it } from "vitest";
import { defaultDraftWorkspaceIntent, useFoldersStore } from "../folders";

const store = () => useFoldersStore.getState();

beforeEach(() => {
  useFoldersStore.setState({
    pendingRenameId: null,
    draftWorkspaceIntent: defaultDraftWorkspaceIntent(),
    importToCloudOpen: false,
    importToCloudPrefill: null,
    borrowToCloudOpen: false,
    borrowToCloudPrefill: null,
    connectGitOpen: false,
    connectGitWsId: null,
  });
});

describe("pending markers", () => {
  it("tracks pending rename independently of draft intent", () => {
    store().setPendingRename("a");
    store().setDraftWorkspaceIntent({ kind: "folder", folderId: "b" });
    expect(store().pendingRenameId).toBe("a");
    expect(store().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "b",
    });

    store().setPendingRename(null);
    expect(store().pendingRenameId).toBeNull();
    expect(store().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "b",
    });
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
  it("defaults to quick_cloud (桌面裸聊默认切云)", () => {
    expect(defaultDraftWorkspaceIntent()).toEqual({ kind: "quick_cloud" });
  });
});

describe("import / connect git dialog flags", () => {
  it("openImportToCloud toggles independently of connectGit", () => {
    store().openImportToCloud();
    expect(store().importToCloudOpen).toBe(true);
    expect(store().connectGitOpen).toBe(false);
    store().closeImportToCloud();
    expect(store().importToCloudOpen).toBe(false);

    store().openConnectGit("folder:x");
    expect(store().connectGitOpen).toBe(true);
    expect(store().connectGitWsId).toBe("folder:x");
    store().closeConnectGit();
    expect(store().connectGitOpen).toBe(false);
    expect(store().connectGitWsId).toBeNull();
  });

  it("openImportToCloud accepts legacy localRootId prefill and clears on close", () => {
    store().openImportToCloud({
      rootId: "root-legacy",
      folderName: "旧文件夹",
    });
    expect(store().importToCloudOpen).toBe(true);
    expect(store().importToCloudPrefill).toEqual({
      rootId: "root-legacy",
      folderName: "旧文件夹",
    });
    store().closeImportToCloud();
    expect(store().importToCloudOpen).toBe(false);
    expect(store().importToCloudPrefill).toBeNull();
  });

  it("openBorrowToCloud toggles independently of import", () => {
    store().openImportToCloud();
    store().openBorrowToCloud();
    expect(store().borrowToCloudOpen).toBe(true);
    expect(store().importToCloudOpen).toBe(true);
    store().closeBorrowToCloud();
    expect(store().borrowToCloudOpen).toBe(false);
    expect(store().importToCloudOpen).toBe(true);
  });

  it("openBorrowToCloud accepts Composer path prefill and clears on close", () => {
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
