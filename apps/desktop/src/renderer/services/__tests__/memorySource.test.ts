import { beforeEach, describe, expect, it, vi } from "vitest";

// The memory FileSource is a thin path-aware dispatcher over the REST client: each
// synthetic leaf path must resolve to the right (kind/slug, scope) and call the matching
// service. Mock the service so the test pins the parsing + dispatch (the only real logic).
vi.mock("@/services/memory", () => ({
  getMemoryFile: vi.fn(() =>
    Promise.resolve({ content: "core", version: "vF" }),
  ),
  writeMemoryFile: vi.fn(() =>
    Promise.resolve({ ok: true, version: "wF", conflict: false }),
  ),
  getMemoryTopic: vi.fn(() =>
    Promise.resolve({ content: "topic", version: "vT" }),
  ),
  writeMemoryTopic: vi.fn(() =>
    Promise.resolve({ ok: true, version: "wT", conflict: false }),
  ),
}));

import {
  getMemoryFile,
  getMemoryTopic,
  writeMemoryFile,
  writeMemoryTopic,
} from "@/services/memory";
import {
  GLOBAL_PREFERENCES_PATH,
  GLOBAL_PROFILE_PATH,
  createMemorySource,
  isAccountMemoryTarget,
  isMemoryTopicPath,
  memoryLeafTabName,
  memoryProjectNavigationPath,
  memoryProjectProfilePath,
  memoryTopicPath,
  parseProjectMemoryFolderId,
} from "@/services/sources/memorySource";

const src = createMemorySource();
const readForEdit = src.readForEdit;
const writeText = src.writeText;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("memorySource leaf dispatch", () => {
  it("exposes the editor hooks the detail pane relies on", () => {
    expect(readForEdit).toBeDefined();
    expect(writeText).toBeDefined();
  });

  it("routes global 偏好/画像 to the per-leaf file API (global scope)", async () => {
    await readForEdit?.(GLOBAL_PREFERENCES_PATH);
    expect(getMemoryFile).toHaveBeenCalledWith("preferences", null);
    await readForEdit?.(GLOBAL_PROFILE_PATH);
    expect(getMemoryFile).toHaveBeenCalledWith("profile", null);
    expect(getMemoryTopic).not.toHaveBeenCalled();
  });

  it("routes a project 画像 leaf to the file API with its folderId", async () => {
    await readForEdit?.(memoryProjectProfilePath("F1"));
    expect(getMemoryFile).toHaveBeenCalledWith("profile", "F1");
  });

  it("routes a project 导航 leaf to the file API with its folderId", async () => {
    await readForEdit?.(memoryProjectNavigationPath("F1"));
    expect(getMemoryFile).toHaveBeenCalledWith("navigation", "F1");
  });

  it("routes topic leaves (global + project) to the topic API", async () => {
    await readForEdit?.(memoryTopicPath(null, "部署流程"));
    expect(getMemoryTopic).toHaveBeenCalledWith("部署流程", null);
    await readForEdit?.(memoryTopicPath("F1", "调试配方"));
    expect(getMemoryTopic).toHaveBeenCalledWith("调试配方", "F1");
    expect(getMemoryFile).not.toHaveBeenCalled();
  });

  it("forwards the CAS baseline on write to the matching API", async () => {
    await writeText?.(memoryTopicPath("F1", "笔记"), {
      content: "body",
      encoding: "utf-8",
      eol: "lf",
      baseline: { etag: "base" },
    });
    expect(writeMemoryTopic).toHaveBeenCalledWith("笔记", "body", "base", "F1");

    await writeText?.(GLOBAL_PROFILE_PATH, {
      content: "p",
      encoding: "utf-8",
      eol: "lf",
      baseline: null,
    });
    expect(writeMemoryFile).toHaveBeenCalledWith("profile", "p", null, null);
  });

  it("maps a write conflict into the source-agnostic conflict result", async () => {
    vi.mocked(writeMemoryTopic).mockResolvedValueOnce({
      ok: false,
      version: "live",
      conflict: true,
    });
    const r = await writeText?.(memoryTopicPath(null, "x"), {
      content: "y",
      encoding: "utf-8",
      eol: "lf",
      baseline: { etag: "stale" },
    });
    expect(r).toEqual({
      ok: false,
      reason: "conflict",
      version: { etag: "live" },
    });
  });
});

describe("parseProjectMemoryFolderId", () => {
  it("extracts folderId from project profile, navigation, and topic paths", () => {
    expect(parseProjectMemoryFolderId(memoryProjectProfilePath("F1"))).toBe(
      "F1",
    );
    expect(parseProjectMemoryFolderId(memoryProjectNavigationPath("F1"))).toBe(
      "F1",
    );
    expect(parseProjectMemoryFolderId(memoryTopicPath("F1", "部署"))).toBe(
      "F1",
    );
  });

  it("returns null for global leaves", () => {
    expect(parseProjectMemoryFolderId(GLOBAL_PREFERENCES_PATH)).toBeNull();
    expect(parseProjectMemoryFolderId(GLOBAL_PROFILE_PATH)).toBeNull();
    expect(parseProjectMemoryFolderId(memoryTopicPath(null, "x"))).toBeNull();
  });
});

describe("isAccountMemoryTarget", () => {
  it("treats global leaves as account-layer, folder leaves as files-page", () => {
    expect(isAccountMemoryTarget(GLOBAL_PREFERENCES_PATH)).toBe(true);
    expect(isAccountMemoryTarget(GLOBAL_PROFILE_PATH, null)).toBe(true);
    expect(isAccountMemoryTarget(memoryTopicPath(null, "x"))).toBe(true);
    expect(isAccountMemoryTarget(memoryProjectProfilePath("F1"))).toBe(false);
    expect(isAccountMemoryTarget("broken-target", "F99")).toBe(false);
  });
});

describe("memoryLeafTabName", () => {
  it("names navigation leaves 导航.md", () => {
    expect(memoryLeafTabName(memoryProjectNavigationPath("F1"))).toBe(
      "导航.md",
    );
  });
});

describe("isMemoryTopicPath", () => {
  it("detects global and project topic leaves", () => {
    expect(isMemoryTopicPath(memoryTopicPath(null, "习惯"))).toBe(true);
    expect(isMemoryTopicPath(memoryTopicPath("F1", "部署"))).toBe(true);
    expect(isMemoryTopicPath(GLOBAL_PROFILE_PATH)).toBe(false);
    expect(isMemoryTopicPath(memoryProjectProfilePath("F1"))).toBe(false);
    expect(isMemoryTopicPath(memoryProjectNavigationPath("F1"))).toBe(false);
  });
});
