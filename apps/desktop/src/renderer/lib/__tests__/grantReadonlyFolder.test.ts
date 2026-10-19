// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  formatGrantReadonlyFolderAnswer,
  pickAndGrantReadonlyFolder,
} from "../grantReadonlyFolder";

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => true),
}));

vi.mock("@/lib/revokeExternalGrant", () => ({
  revokeExternalGrant: vi.fn(),
}));

vi.mock("@/services/externalGrants", () => ({
  invalidateExternalGrants: vi.fn(),
}));

vi.mock("@/services/permissionAxes", () => ({
  peekConversationBoundary: vi.fn(async () => undefined),
}));

vi.mock("@/services/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  api: {
    post: vi.fn(),
  },
}));

describe("formatGrantReadonlyFolderAnswer", () => {
  it("mentions readonly session scope without absolute paths", () => {
    const text = formatGrantReadonlyFolderAnswer(
      "授权只读访问",
      "6月报表",
      "external/6月报表",
    );
    expect(text).toContain("只读");
    expect(text).toContain("仅本次对话");
    expect(text).toContain("可撤销");
    expect(text).toContain("external/6月报表");
    expect(text).not.toMatch(/^[A-Za-z]:\\/);
  });
});

describe("pickAndGrantReadonlyFolder 别名以回执为准", () => {
  beforeEach(async () => {
    const { api } = await import("@/services/api");
    vi.mocked(api.post).mockReset();
    window.fsApi = {
      grantSessionReadonlyRoot: vi.fn(),
      adoptSessionRootAlias: vi.fn(async () => true),
    } as unknown as typeof window.fsApi;
  });

  it("登记不猜别名，回执里的那个写到本机根上", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "报告", mode: "readonly" },
    });
    vi.mocked(api.post).mockResolvedValue({
      grant: { alias: "ext_mfrggzdf", namespace: "external/ext_mfrggzdf" },
    });

    const result = await pickAndGrantReadonlyFolder("conv-1", {
      wellKnown: "documents",
      targetName: "报告",
    });

    // 别名是服务端 mint 的命名空间，桌面没有第二套算法去提议它
    expect(api.post).toHaveBeenCalledWith(
      "/v1/conversations/conv-1/workspace/external-grants",
      { root_id: "r1", label: "报告" },
    );
    expect(window.fsApi.adoptSessionRootAlias).toHaveBeenCalledWith(
      "conv-1",
      "r1",
      "ext_mfrggzdf",
    );
    expect(result.ok && result.alias).toBe("ext_mfrggzdf");
  });

  it("登记失败不写回（本机授权已撤回）", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "报告", mode: "readonly" },
    });
    vi.mocked(api.post).mockRejectedValue(new Error("boom"));

    const result = await pickAndGrantReadonlyFolder("conv-1", {
      path: "C:\\报告",
    });

    expect(result.ok).toBe(false);
    expect(window.fsApi.adoptSessionRootAlias).not.toHaveBeenCalled();
  });

  it("别名写不下去 → 撤回授权并回失败（没有别名就没有可用挂载）", async () => {
    const { api } = await import("@/services/api");
    const { revokeExternalGrant } = await import("@/lib/revokeExternalGrant");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "报告", mode: "readonly" },
    });
    vi.mocked(api.post).mockResolvedValue({
      grant: { alias: "ext_mfrggzdf", namespace: "external/ext_mfrggzdf" },
    });
    vi.mocked(window.fsApi.adoptSessionRootAlias).mockResolvedValue(false);

    const result = await pickAndGrantReadonlyFolder("conv-1", {
      path: "C:\\报告",
    });

    expect(result.ok).toBe(false);
    expect(revokeExternalGrant).toHaveBeenCalledWith("conv-1", "r1");
  });
});

describe("pickAndGrantReadonlyFolder resolve failures", () => {
  beforeEach(() => {
    window.fsApi = {
      grantSessionReadonlyRoot: vi.fn(),
    } as unknown as typeof window.fsApi;
  });

  it("maps not_found (≠ cancelled)", async () => {
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });
    const result = await pickAndGrantReadonlyFolder("conv-1", {
      wellKnown: "desktop",
      targetName: "失踪",
    });
    expect(result).toEqual({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });
  });

  it("maps not_directory when path hits a file", async () => {
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: false,
      reason: "not_directory",
      message: "路径指向的是文件，不是目录",
    });
    const result = await pickAndGrantReadonlyFolder("conv-1", {
      path: "C:\\tmp\\file.txt",
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toBe("not_directory");
  });

  it("blank hints still invoke IPC and surface not_found (no silent cancel)", async () => {
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });
    const result = await pickAndGrantReadonlyFolder("conv-1");
    expect(window.fsApi.grantSessionReadonlyRoot).toHaveBeenCalledWith({
      conversationId: "conv-1",
      mode: "readonly",
    });
    expect(result).toEqual({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });
  });
});
