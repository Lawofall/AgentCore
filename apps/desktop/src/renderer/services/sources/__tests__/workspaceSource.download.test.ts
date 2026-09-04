// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

const {
  downloadWorkspaceFile,
  downloadWorkspaceArchive,
  wsDownloadFile,
  wsDownloadArchive,
} = vi.hoisted(() => ({
  downloadWorkspaceFile: vi.fn(async () => undefined),
  downloadWorkspaceArchive: vi.fn(async () => undefined),
  wsDownloadFile: vi.fn(async () => undefined),
  wsDownloadArchive: vi.fn(async () => undefined),
}));

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveConversationLocalTarget: vi.fn(),
}));
vi.mock("@/services/workspace", () => ({
  listWorkspaceFiles: vi.fn(),
  readWorkspaceFile: vi.fn(),
  readWorkspaceFileForEdit: vi.fn(),
  writeWorkspaceFileText: vi.fn(),
  uploadWorkspaceFile: vi.fn(),
  createWorkspaceDir: vi.fn(),
  moveWorkspaceFile: vi.fn(),
  copyWorkspaceFile: vi.fn(),
  deleteWorkspaceFile: vi.fn(),
  downloadWorkspaceFile,
  downloadWorkspaceArchive,
  exportWorkspaceMdToDocx: vi.fn(),
  openWorkspaceInBrowser: vi.fn(),
}));
vi.mock("@/services/workspaces", () => ({
  wsListFiles: vi.fn(),
  wsReadFile: vi.fn(),
  wsReadFileForEdit: vi.fn(),
  wsWriteFileText: vi.fn(),
  wsUploadFile: vi.fn(),
  wsCreateDir: vi.fn(),
  wsMoveFile: vi.fn(),
  wsCopyFile: vi.fn(),
  wsDeleteFile: vi.fn(),
  wsDownloadFile,
  wsDownloadArchive,
  wsExportMdToDocx: vi.fn(),
  wsListFileIndex: vi.fn(async () => ({ files: [], truncated: false })),
  openCloudWorkspaceInBrowser: vi.fn(),
}));
vi.mock("@/lib/openWorkspaceHtmlInBrowser", () => ({
  openWorkspaceHtmlInBrowser: vi.fn(),
}));
vi.mock("@/lib/capabilities", () => ({
  hasInAppPreview: () => false,
}));

import {
  createCloudWorkspaceSource,
  createWorkspaceSource,
} from "@/services/sources/workspaceSource";

describe("cloud FileSource.download 文件夹走 archive", () => {
  afterEach(() => {
    downloadWorkspaceFile.mockReset();
    downloadWorkspaceArchive.mockReset();
    wsDownloadFile.mockReset();
    wsDownloadArchive.mockReset();
  });

  it("conversation：文件走 files，目录走 archive", async () => {
    const source = createWorkspaceSource("c1");
    await source.download?.("notes.md", "notes.md");
    expect(downloadWorkspaceFile).toHaveBeenCalledWith(
      "c1",
      "notes.md",
      "notes.md",
    );
    expect(downloadWorkspaceArchive).not.toHaveBeenCalled();

    await source.download?.("docs", "docs.zip", { isDir: true });
    expect(downloadWorkspaceArchive).toHaveBeenCalledWith(
      "c1",
      "docs",
      "docs.zip",
    );
  });

  it("只读 folder:：目录走 archive 而不是 snapshots", async () => {
    const source = createCloudWorkspaceSource("folder:f1", "项目", {
      readonly: true,
    });
    expect(source.caps.transfer).toBe(true);
    expect(source.caps.snapshots).toBe(false);
    await source.download?.("pack", "pack.zip", { isDir: true });
    expect(wsDownloadArchive).toHaveBeenCalledWith(
      "folder:f1",
      "pack",
      "pack.zip",
    );
    expect(wsDownloadFile).not.toHaveBeenCalled();
  });
});
