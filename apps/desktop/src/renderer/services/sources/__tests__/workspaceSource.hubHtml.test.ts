// @vitest-environment jsdom

import type { FsApi } from "@shared/ipc-contract";
import { afterEach, describe, expect, it, vi } from "vitest";

const {
  openCloudWorkspaceInBrowser,
  openWorkspaceInBrowser,
  openWorkspaceHtmlInBrowser,
} = vi.hoisted(() => ({
  openCloudWorkspaceInBrowser: vi.fn(),
  openWorkspaceInBrowser: vi.fn(),
  openWorkspaceHtmlInBrowser: vi.fn(),
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
  downloadWorkspaceFile: vi.fn(),
  downloadWorkspaceArchive: vi.fn(),
  exportWorkspaceMdToDocx: vi.fn(),
  openWorkspaceInBrowser,
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
  wsDownloadFile: vi.fn(),
  wsDownloadArchive: vi.fn(),
  wsExportMdToDocx: vi.fn(),
  wsListFileIndex: vi.fn(async () => ({ files: [], truncated: false })),
  openCloudWorkspaceInBrowser,
}));
vi.mock("@/lib/openWorkspaceHtmlInBrowser", () => ({
  openWorkspaceHtmlInBrowser,
}));
vi.mock("@/lib/capabilities", () => ({
  hasInAppPreview: vi.fn(() => false),
}));

import { hasInAppPreview } from "@/lib/capabilities";
import { createCloudWorkspaceSource } from "@/services/sources/workspaceSource";

describe("createCloudWorkspaceSource — hub HTML 外开 / 完整预览", () => {
  afterEach(() => {
    window.fsApi = undefined as unknown as FsApi;
    openCloudWorkspaceInBrowser.mockReset();
    openWorkspaceInBrowser.mockReset();
    openWorkspaceHtmlInBrowser.mockReset();
    vi.mocked(hasInAppPreview).mockReturnValue(false);
  });

  it("folder: + previewArchive → 挂 openInBrowser（ws 快照路径）", async () => {
    window.fsApi = { previewArchive: vi.fn() } as unknown as FsApi;
    const source = createCloudWorkspaceSource("folder:f1", "项目");
    expect(typeof source.openInBrowser).toBe("function");
    expect(source.openInAppPreview).toBeUndefined();

    await source.openInBrowser?.("site/index.html");
    expect(openCloudWorkspaceInBrowser).toHaveBeenCalledWith(
      "folder:f1",
      "site/index.html",
    );
    expect(openWorkspaceInBrowser).not.toHaveBeenCalled();
  });

  it("folder: + hasInAppPreview → 挂 openInAppPreview（跟 folder: desk）", async () => {
    vi.mocked(hasInAppPreview).mockReturnValue(true);
    const source = createCloudWorkspaceSource("folder:f1", "项目");
    expect(typeof source.openInAppPreview).toBe("function");

    await source.openInAppPreview?.("site/index.html");
    expect(openWorkspaceHtmlInBrowser).toHaveBeenCalledWith(
      "f1",
      "site/index.html",
      "folder:f1",
    );
  });

  it("conv: + previewArchive → 会话快照路径（非 ws 快照）", async () => {
    window.fsApi = { previewArchive: vi.fn() } as unknown as FsApi;
    const source = createCloudWorkspaceSource("conv:c9", "草稿");
    await source.openInBrowser?.("a.html");
    expect(openWorkspaceInBrowser).toHaveBeenCalledWith("c9", "a.html");
    expect(openCloudWorkspaceInBrowser).not.toHaveBeenCalled();
  });

  it("conv: + hasInAppPreview → 完整预览传 conv: desk", async () => {
    vi.mocked(hasInAppPreview).mockReturnValue(true);
    const source = createCloudWorkspaceSource("conv:c9", "草稿");
    await source.openInAppPreview?.("a.html");
    expect(openWorkspaceHtmlInBrowser).toHaveBeenCalledWith(
      "c9",
      "a.html",
      "conv:c9",
    );
  });

  it("shared: → 不挂 openInBrowser / openInAppPreview", () => {
    window.fsApi = { previewArchive: vi.fn() } as unknown as FsApi;
    vi.mocked(hasInAppPreview).mockReturnValue(true);
    const source = createCloudWorkspaceSource("shared:s1", "共享");
    expect(source.openInBrowser).toBeUndefined();
    expect(source.openInAppPreview).toBeUndefined();
  });

  it("无 previewArchive → 不挂 openInBrowser", () => {
    const source = createCloudWorkspaceSource("folder:f1", "项目");
    expect(source.openInBrowser).toBeUndefined();
  });
});
