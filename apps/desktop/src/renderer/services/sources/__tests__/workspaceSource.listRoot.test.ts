// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

const { listWorkspaceFiles, wsListFiles } = vi.hoisted(() => ({
  listWorkspaceFiles: vi.fn(),
  wsListFiles: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveConversationLocalTarget: vi.fn(),
}));
vi.mock("@/services/workspace", () => ({
  listWorkspaceFiles,
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
  fetchWorkspaceFileBlob: vi.fn(),
  exportWorkspaceMdToDocx: vi.fn(),
  openWorkspaceInBrowser: vi.fn(),
}));
vi.mock("@/services/workspaces", () => ({
  wsListFiles,
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
  wsFetchFileBlob: vi.fn(),
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

describe("cloud FileSource listing (fc35aece root zip visibility)", () => {
  afterEach(() => {
    listWorkspaceFiles.mockReset();
    wsListFiles.mockReset();
  });

  it("root listDir uses non-recursive list so AI zip is not dropped by recursive cap", async () => {
    listWorkspaceFiles.mockImplementation(
      async (_id: string, opts: { recursive?: boolean; dir?: string }) => {
        if (opts.recursive) {
          // Simulate server alphabetical 100-cap: only site/* survives, root zip gone.
          return {
            files: Array.from({ length: 100 }, (_, i) => ({
              path: `site/f${String(i).padStart(3, "0")}.html`,
              isDir: false,
            })),
            truncated: true,
          };
        }
        return {
          files: [
            { path: "site", isDir: true },
            { path: "独立站整改.zip", isDir: false },
          ],
          truncated: false,
        };
      },
    );

    const source = createWorkspaceSource("c1");
    expect(source.listTree).toBeUndefined();

    const root = await source.listDir("");
    expect(listWorkspaceFiles).toHaveBeenCalledWith("c1", {
      recursive: false,
      dir: ".",
    });
    expect(root.map((n) => n.path).sort()).toEqual(["site", "独立站整改.zip"]);
  });

  it("subdir listDir asks the server for that directory only", async () => {
    // Deep files must survive a workspace bigger than the root's entry budget:
    // the old path pulled the recursive tree and filtered locally, so anything
    // past the cap simply did not exist in the panel.
    wsListFiles.mockResolvedValue({
      files: [
        { path: "site/index.html", isDir: false },
        { path: "site/a.css", isDir: false },
      ],
      truncated: false,
    });

    const source = createCloudWorkspaceSource("conv:c1", "工作区");
    const kids = await source.listDir("site");
    expect(wsListFiles).toHaveBeenCalledWith("conv:c1", {
      recursive: false,
      dir: "site",
    });
    expect(kids.map((n) => n.path).sort()).toEqual([
      "site/a.css",
      "site/index.html",
    ]);
  });

  it("listDirBounded passes the server's truncated bit through", async () => {
    wsListFiles.mockResolvedValue({
      files: [{ path: "site/a.css", isDir: false }],
      truncated: true,
    });

    const source = createCloudWorkspaceSource("conv:c1", "工作区");
    const res = await source.listDirBounded?.("site");
    expect(res).toEqual({
      entries: [{ path: "site/a.css", name: "a.css", isDir: false }],
      truncated: true,
    });
  });

  it("AgentCore expand hides path-aware internal zones; bare index/ stays", async () => {
    wsListFiles.mockResolvedValue({
      files: [
        { path: "AgentCore/index", isDir: true },
        { path: "AgentCore/trash", isDir: true },
        { path: "AgentCore/baselines", isDir: true },
        { path: "AgentCore/规则", isDir: true },
      ],
      truncated: false,
    });

    const source = createCloudWorkspaceSource("conv:c1", "工作区");
    const acKids = await source.listDir("AgentCore");
    expect(acKids.map((n) => n.path).sort()).toEqual(["AgentCore/规则"]);

    // Root path also drops leaked zone entries if a payload includes them.
    listWorkspaceFiles.mockResolvedValue({
      files: [
        { path: "AgentCore", isDir: true },
        { path: "AgentCore/index", isDir: true },
        { path: "index", isDir: true },
      ],
      truncated: false,
    });
    const convSource = createWorkspaceSource("c1");
    const rootKids = await convSource.listDir("");
    expect(rootKids.map((n) => n.path).sort()).toEqual(["AgentCore", "index"]);
  });
});
