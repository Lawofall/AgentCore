// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { pickAndGrantOrganizeFolder } from "../grantOrganizeFolder";

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => true),
}));

vi.mock("@/lib/revokeExternalGrant", () => ({
  revokeExternalGrant: vi.fn(),
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

describe("pickAndGrantOrganizeFolder", () => {
  beforeEach(async () => {
    const { api } = await import("@/services/api");
    vi.mocked(api.post).mockReset();
    window.fsApi = {
      grantSessionReadonlyRoot: vi.fn(),
      adoptSessionRootAlias: vi.fn(async () => true),
    } as unknown as typeof window.fsApi;
  });

  it("把回执里的权威别名写回本机根", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "咨询", alias: "d_", mode: "organize" },
    });
    vi.mocked(api.post).mockResolvedValue({
      grant: { alias: "ext_2wcyoa", namespace: "external/ext_2wcyoa" },
    });

    await pickAndGrantOrganizeFolder("conv-1", { path: "C:\\咨询" });

    expect(window.fsApi.adoptSessionRootAlias).toHaveBeenCalledWith(
      "conv-1",
      "r1",
      "ext_2wcyoa",
    );
  });

  it("always requests mode=organize (readonly→write still goes through this confirm path)", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "咨询", alias: "咨询", mode: "organize" },
      displayLabel: "桌面 › 咨询",
    });
    vi.mocked(api.post).mockResolvedValue({
      grant: { alias: "咨询", namespace: "external/咨询" },
    });

    const result = await pickAndGrantOrganizeFolder("conv-1", {
      wellKnown: "desktop",
      targetName: "咨询",
    });

    expect(window.fsApi.grantSessionReadonlyRoot).toHaveBeenCalledWith({
      conversationId: "conv-1",
      mode: "organize",
      wellKnown: "desktop",
      targetName: "咨询",
    });
    expect(api.post).toHaveBeenCalledWith(
      "/v1/conversations/conv-1/workspace/external-grants",
      expect.objectContaining({ mode: "organize" }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.displayLabel).toBe("桌面 › 咨询");
  });

  it("授权没成时不登记（服务端不该记下这台机器没有的根）", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });

    await pickAndGrantOrganizeFolder("conv-1", { path: "C:\\missing" });

    expect(api.post).not.toHaveBeenCalled();
  });

  it("别名没写下来 → 撤回授权并回失败（没有别名就没有可用挂载）", async () => {
    const { api } = await import("@/services/api");
    const { revokeExternalGrant } = await import("@/lib/revokeExternalGrant");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "咨询", mode: "organize" },
    });
    vi.mocked(api.post).mockResolvedValue({
      grant: { alias: "ext_2wcyoa", namespace: "external/ext_2wcyoa" },
    });
    vi.mocked(window.fsApi.adoptSessionRootAlias).mockResolvedValue(false);

    const result = await pickAndGrantOrganizeFolder("conv-1", {
      path: "C:\\咨询",
    });

    expect(result.ok).toBe(false);
    expect(revokeExternalGrant).toHaveBeenCalledWith("conv-1", "r1");
  });

  it("maps not_found without picker/cancel", async () => {
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });
    const result = await pickAndGrantOrganizeFolder("conv-1", {
      wellKnown: "desktop",
      targetName: "失踪",
    });
    expect(result).toEqual({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });
  });

  it("forwards rootId for upgrade (no path)", async () => {
    const { api } = await import("@/services/api");
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "咨询", alias: "desk", mode: "organize" },
    });
    vi.mocked(api.post).mockResolvedValue({
      grant: { alias: "desk", namespace: "external/desk" },
    });

    await pickAndGrantOrganizeFolder("conv-1", { rootId: "r1" });

    expect(window.fsApi.grantSessionReadonlyRoot).toHaveBeenCalledWith({
      conversationId: "conv-1",
      mode: "organize",
      rootId: "r1",
    });
  });

  it("maps cancelled from the system dialog", async () => {
    vi.mocked(window.fsApi.grantSessionReadonlyRoot).mockResolvedValue({
      ok: false,
      reason: "cancelled",
      message: "用户拒绝授权",
    });
    const result = await pickAndGrantOrganizeFolder("conv-1", { rootId: "r1" });
    expect(result).toEqual({
      ok: false,
      reason: "cancelled",
      message: "用户拒绝授权",
    });
  });
});
