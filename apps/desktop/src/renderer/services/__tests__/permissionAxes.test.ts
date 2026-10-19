// @vitest-environment jsdom
import { hasLocalEngine } from "@/lib/capabilities";
import { api } from "@/services/api";
import {
  BOUNDARY_LABELS,
  BOUNDARY_ORDER,
  DEFAULT_PERMISSION_AXES,
  axesEqual,
  boundaryShortLabel,
  clearDefaultPermissionAxesCache,
  factoryPermissionAxes,
  needsComputerConfirm,
  needsFullConfirm,
  normalizeAxes,
  permissionAxesShortLabel,
  rememberBoundarySelection,
  resolveDefaultPermissionAxes,
  setComposerDraftAxes,
} from "@/services/permissionAxes";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", () => ({
  hasLocalEngine: vi.fn(() => false),
}));
vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(() => Promise.resolve({ policy: "folder" })),
  },
}));

describe("workspace boundary", () => {
  it("defaults to computer and only accepts the four ids", () => {
    expect(DEFAULT_PERMISSION_AXES).toEqual({ boundary: "computer" });
    expect(BOUNDARY_ORDER).toEqual(["read", "folder", "computer", "full"]);
    expect(normalizeAxes(undefined)).toEqual({ boundary: "computer" });
    expect(normalizeAxes({})).toEqual({ boundary: "computer" });
    expect(
      normalizeAxes({ file_write: "ask", command: "ask", host: "off" }),
    ).toEqual({ boundary: "computer" });
    expect(normalizeAxes({ boundary: "read" })).toEqual({ boundary: "read" });
    expect(normalizeAxes({ boundary: "nope" })).toEqual({
      boundary: "computer",
    });
  });

  it("confirms when moving onto 这台电脑 or 完全权限", () => {
    const read = { boundary: "read" as const };
    const folder = { boundary: "folder" as const };
    const computer = { boundary: "computer" as const };
    const full = { boundary: "full" as const };
    expect(needsComputerConfirm(read, folder)).toBe(false);
    expect(needsComputerConfirm(folder, read)).toBe(false);
    expect(needsComputerConfirm(read, computer)).toBe(true);
    expect(needsComputerConfirm(folder, computer)).toBe(true);
    expect(needsComputerConfirm(computer, computer)).toBe(false);
    expect(needsComputerConfirm(computer, folder)).toBe(false);
    expect(needsComputerConfirm(full, computer)).toBe(false);
    expect(needsFullConfirm(folder, full)).toBe(true);
    expect(needsFullConfirm(computer, full)).toBe(true);
    expect(needsFullConfirm(full, full)).toBe(false);
    expect(needsFullConfirm(full, folder)).toBe(false);
  });

  it("labels only current boundary ids", () => {
    expect(boundaryShortLabel("read")).toBe("只看");
    expect(boundaryShortLabel("folder")).toBe("本文件夹");
    expect(boundaryShortLabel("computer")).toBe("这台电脑");
    expect(boundaryShortLabel("full")).toBe("完全权限");
    expect(permissionAxesShortLabel({ boundary: "folder" })).toBe("本文件夹");
    expect(permissionAxesShortLabel("read")).toBe("只看");
    expect(permissionAxesShortLabel('{"boundary":"computer"}')).toBe(
      "这台电脑",
    );
    expect(permissionAxesShortLabel("less_interrupt")).toBeNull();
    expect(
      permissionAxesShortLabel({
        file_write: "session",
        command: "auto",
        host: "session",
      }),
    ).toBeNull();
    expect(permissionAxesShortLabel("not json")).toBeNull();
  });

  it("keeps short labels and equality", () => {
    for (const id of BOUNDARY_ORDER) {
      expect(BOUNDARY_LABELS[id].short.length).toBeGreaterThan(0);
      expect(axesEqual({ boundary: id }, { boundary: id })).toBe(true);
    }
    expect(axesEqual({ boundary: "read" }, { boundary: "folder" })).toBe(false);
    expect(BOUNDARY_LABELS.computer.description).toContain("这台电脑上的文件");
    expect(BOUNDARY_LABELS.full.description).toContain("不再询问");
    expect(BOUNDARY_LABELS.full.description).toContain("格式化磁盘");
  });
});

describe("last-used boundary", () => {
  beforeEach(() => {
    clearDefaultPermissionAxesCache();
    setComposerDraftAxes(null);
    vi.mocked(hasLocalEngine).mockReturnValue(false);
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.put).mockResolvedValue({ policy: "folder" });
  });

  afterEach(() => {
    clearDefaultPermissionAxesCache();
    setComposerDraftAxes(null);
  });

  it("seeds new chats from the last pick without hitting the account", async () => {
    rememberBoundarySelection("read");
    await expect(resolveDefaultPermissionAxes()).resolves.toEqual({
      boundary: "read",
    });
    expect(api.get).not.toHaveBeenCalled();
    expect(api.put).toHaveBeenCalledWith("/v1/users/me/autonomy", {
      policy: "read",
    });
  });

  it("factory is 本文件夹 on web and 这台电脑 on a local engine", () => {
    expect(factoryPermissionAxes()).toEqual({ boundary: "folder" });
    vi.mocked(hasLocalEngine).mockReturnValue(true);
    expect(factoryPermissionAxes()).toEqual({ boundary: "computer" });
  });

  it("seeds factory 本文件夹 on web without hitting the account", async () => {
    await expect(resolveDefaultPermissionAxes()).resolves.toEqual({
      boundary: "folder",
    });
    expect(api.get).not.toHaveBeenCalled();
  });

  it("seeds factory 这台电脑 on a local engine without hitting the account", async () => {
    vi.mocked(hasLocalEngine).mockReturnValue(true);
    await expect(resolveDefaultPermissionAxes()).resolves.toEqual({
      boundary: "computer",
    });
    expect(api.get).not.toHaveBeenCalled();
  });

  it("does not open a new web chat on 这台电脑", async () => {
    rememberBoundarySelection("computer");
    await expect(resolveDefaultPermissionAxes()).resolves.toEqual({
      boundary: "folder",
    });
  });

  it("keeps 完全权限 on web", async () => {
    rememberBoundarySelection("full");
    await expect(resolveDefaultPermissionAxes()).resolves.toEqual({
      boundary: "full",
    });
  });

  it("keeps 这台电脑 on a local engine", async () => {
    vi.mocked(hasLocalEngine).mockReturnValue(true);
    rememberBoundarySelection("computer");
    await expect(resolveDefaultPermissionAxes()).resolves.toEqual({
      boundary: "computer",
    });
  });
});
