// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

const { copyWorkspaceFile, wsCopyFile } = vi.hoisted(() => ({
  copyWorkspaceFile: vi.fn(async () => undefined),
  wsCopyFile: vi.fn(async () => undefined),
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
  copyWorkspaceFile,
  deleteWorkspaceFile: vi.fn(),
  downloadWorkspaceFile: vi.fn(),
  downloadWorkspaceArchive: vi.fn(),
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
  wsCopyFile,
  wsDeleteFile: vi.fn(),
  wsDownloadFile: vi.fn(),
  wsDownloadArchive: vi.fn(),
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

describe("cloud FileSource.copy", () => {
  afterEach(() => {
    copyWorkspaceFile.mockReset();
    wsCopyFile.mockReset();
  });

  it("conversation source exposes copy and posts via conversation REST", async () => {
    const source = createWorkspaceSource("c1");
    expect(source.caps.edit).toBe(true);
    expect(source.copy).toBeTypeOf("function");
    await source.copy?.("a.txt", "a 副本.txt");
    expect(copyWorkspaceFile).toHaveBeenCalledWith("c1", "a.txt", "a 副本.txt");
  });

  it("ws-id source exposes copy and posts via workspaces REST", async () => {
    const source = createCloudWorkspaceSource("conv:c1");
    expect(source.caps.edit).toBe(true);
    expect(source.copy).toBeTypeOf("function");
    await source.copy?.("tree", "tree2");
    expect(wsCopyFile).toHaveBeenCalledWith("conv:c1", "tree", "tree2");
  });

  it("readonly ws-id source omits copy (aligned with caps.edit)", () => {
    const source = createCloudWorkspaceSource("shared:s1", "共享空间", {
      readonly: true,
    });
    expect(source.caps.edit).toBe(false);
    expect(source.copy).toBeUndefined();
    expect(source.exportMdToDocx).toBeUndefined();
  });
});
