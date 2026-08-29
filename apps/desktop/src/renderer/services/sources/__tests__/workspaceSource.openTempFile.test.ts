// @vitest-environment jsdom

/**
 * 云端源的「用本机默认应用打开」：桌面取字节 → 只读临时副本 → 系统程序，web 不挂入口。
 *
 * 覆盖的是这条链上「不该漂移」的三点：能力条件挂载（web 不该出现入口）、白名单谓词
 * （云端字节是 AI 产出的，名单外连入口都不给）、成功后必须说明打开的是副本。
 */

import type { FsApi } from "@shared/ipc-contract";
import { OPEN_TEMP_FILE_MAX_BYTES } from "@shared/ipc-contract";
import { afterEach, describe, expect, it, vi } from "vitest";

const { fetchWorkspaceFileBlob, wsFetchFileBlob, notifyInfo } = vi.hoisted(
  () => ({
    fetchWorkspaceFileBlob: vi.fn(),
    wsFetchFileBlob: vi.fn(),
    notifyInfo: vi.fn(),
  }),
);

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveConversationLocalTarget: vi.fn(),
}));
vi.mock("@/lib/capabilities", () => ({ hasInAppPreview: vi.fn(() => false) }));
vi.mock("@/lib/openWorkspaceHtmlInBrowser", () => ({
  openWorkspaceHtmlInBrowser: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({ notifyInfo }));
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
  openWorkspaceInBrowser: vi.fn(),
  fetchWorkspaceFileBlob,
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
  openCloudWorkspaceInBrowser: vi.fn(),
  wsFetchFileBlob,
}));

import {
  createCloudWorkspaceSource,
  createWorkspaceSource,
} from "@/services/sources/workspaceSource";

/** 挂上一个桌面 `fsApi.openTempFile` 桩并返回它。 */
function stubOpenTempFile(
  result: Awaited<ReturnType<NonNullable<FsApi["openTempFile"]>>> = {
    ok: true,
  },
) {
  const openTempFile = vi.fn(async () => result);
  window.fsApi = { openTempFile } as unknown as FsApi;
  return openTempFile;
}

afterEach(() => {
  window.fsApi = undefined as unknown as FsApi;
  fetchWorkspaceFileBlob.mockReset();
  wsFetchFileBlob.mockReset();
  notifyInfo.mockReset();
});

describe("云端源 · 用本机默认应用打开（条件挂载）", () => {
  it("桌面（有 openTempFile）→ 会话源与 hub 源都挂方法 + 谓词", () => {
    stubOpenTempFile();

    const conv = createWorkspaceSource("c1");
    expect(typeof conv.openWithOsDefaultApp).toBe("function");
    expect(typeof conv.canOpenWithOsDefaultApp).toBe("function");

    const hub = createCloudWorkspaceSource("folder:f1", "项目");
    expect(typeof hub.openWithOsDefaultApp).toBe("function");
    expect(typeof hub.canOpenWithOsDefaultApp).toBe("function");
  });

  it("web（无 fsApi.openTempFile）→ 两者都不挂，入口整个不出现", () => {
    expect(createWorkspaceSource("c1").openWithOsDefaultApp).toBeUndefined();
    expect(createWorkspaceSource("c1").canOpenWithOsDefaultApp).toBeUndefined();

    window.fsApi = {} as unknown as FsApi;
    const hub = createCloudWorkspaceSource("folder:f1", "项目");
    expect(hub.openWithOsDefaultApp).toBeUndefined();
    expect(hub.canOpenWithOsDefaultApp).toBeUndefined();
  });

  it("只读共享空间也能打开（读字节不是写操作）", () => {
    stubOpenTempFile();
    const shared = createCloudWorkspaceSource("shared:s1", "共享", {
      readonly: true,
    });
    expect(typeof shared.openWithOsDefaultApp).toBe("function");
  });
});

describe("云端源 · 白名单谓词", () => {
  it("名单内放行、名单外拒绝（含无扩展名与 Windows 尾点伪装）", () => {
    stubOpenTempFile();
    const can = createWorkspaceSource("c1").canOpenWithOsDefaultApp;
    expect(can).toBeDefined();
    if (!can) return;

    expect(can("dir/report.pdf")).toBe(true);
    expect(can("表格.XLSX")).toBe(true);
    expect(can("dir/tool.exe")).toBe(false);
    expect(can("build.ps1")).toBe(false);
    expect(can("宏.docm")).toBe(false);
    expect(can("README")).toBe(false);
    expect(can("evil.exe.")).toBe(false);
  });
});

describe("云端源 · 打开临时副本", () => {
  it("取字节 → openTempFile(文件名, 字节) → 提示这是只读副本", async () => {
    const openTempFile = stubOpenTempFile();
    fetchWorkspaceFileBlob.mockResolvedValue(new Blob(["hello"]));

    await createWorkspaceSource("c1").openWithOsDefaultApp?.("dir/report.pdf");

    expect(fetchWorkspaceFileBlob).toHaveBeenCalledWith("c1", "dir/report.pdf");
    expect(openTempFile).toHaveBeenCalledTimes(1);
    const [suggestedName, bytes] = openTempFile.mock.calls[0] as unknown as [
      string,
      Uint8Array,
    ];
    expect(suggestedName).toBe("report.pdf");
    expect(new TextDecoder().decode(bytes)).toBe("hello");

    expect(notifyInfo).toHaveBeenCalledTimes(1);
    const [title, opts] = notifyInfo.mock.calls[0] as [
      string,
      { description?: string },
    ];
    expect(title).toContain("打开");
    expect(opts?.description).toContain("只读副本");
    expect(opts?.description).toMatch(/不会同步回云端|不会同步/);
  });

  it("hub 源按 ws id 取字节（与会话源同一实现，只差寻址）", async () => {
    const openTempFile = stubOpenTempFile();
    wsFetchFileBlob.mockResolvedValue(new Blob(["x"]));

    await createCloudWorkspaceSource(
      "folder:f1",
      "项目",
    ).openWithOsDefaultApp?.("a.png");

    expect(wsFetchFileBlob).toHaveBeenCalledWith("folder:f1", "a.png");
    expect(openTempFile).toHaveBeenCalledWith("a.png", expect.any(Uint8Array));
  });

  it("主进程拒绝 → 抛出其 message 供调用方 toast，不误报成功", async () => {
    stubOpenTempFile({
      ok: false,
      reason: "unsupported_type",
      message: "不支持的文件类型",
    });
    fetchWorkspaceFileBlob.mockResolvedValue(new Blob(["x"]));

    await expect(
      createWorkspaceSource("c1").openWithOsDefaultApp?.("a.pdf"),
    ).rejects.toThrow("不支持的文件类型");
    expect(notifyInfo).not.toHaveBeenCalled();
  });

  it("超过字节上限 → 就地拦下并指向「下载」，不发起 IPC", async () => {
    const openTempFile = stubOpenTempFile();
    fetchWorkspaceFileBlob.mockResolvedValue({
      size: OPEN_TEMP_FILE_MAX_BYTES + 1,
    } as Blob);

    await expect(
      createWorkspaceSource("c1").openWithOsDefaultApp?.("huge.zip"),
    ).rejects.toThrow(/下载/);
    expect(openTempFile).not.toHaveBeenCalled();
  });
});
