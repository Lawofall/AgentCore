import type { FsApi, FsErrorCode, FsResult } from "@shared/ipc-contract";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  LocalFsError,
  createLocalRootSource,
} from "../sources/localRootSource";

function fail(code: FsErrorCode, reason: string): FsResult<never> {
  return { ok: false, code, reason };
}

describe("createLocalRootSource lazy workspace", () => {
  let listDir: ReturnType<typeof vi.fn>;
  let listFiles: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    listDir = vi.fn();
    listFiles = vi.fn();
    (globalThis as unknown as { window: { fsApi: Partial<FsApi> } }).window = {
      fsApi: {
        listDir: listDir as FsApi["listDir"],
        listFiles: listFiles as FsApi["listFiles"],
        readFile: vi.fn(),
        watch: vi.fn(),
        unwatch: vi.fn(),
        onChanged: () => () => {},
      },
    };
  });

  it("subpath source: base not_found → listDir returns []", async () => {
    listDir.mockResolvedValue(fail("not_found", "文件或目录不存在"));
    const src = createLocalRootSource("r1", "chat", "我的对话");
    await expect(src.listDir("")).resolves.toEqual([]);
    expect(listDir).toHaveBeenCalledWith("r1", "我的对话");
  });

  it("subpath source: base not_found → listFileIndex returns empty listing", async () => {
    listDir.mockResolvedValue(fail("not_found", "文件或目录不存在"));
    const src = createLocalRootSource("r1", "chat", "我的对话");
    expect(src.listFileIndex).toBeDefined();
    const listFileIndex = src.listFileIndex;
    if (listFileIndex == null) throw new Error("expected listFileIndex");
    await expect(listFileIndex()).resolves.toEqual({
      files: [],
      truncated: false,
    });
    expect(listFiles).not.toHaveBeenCalled();
  });

  it("empty subpath: root not_found → listDir throws LocalFsError", async () => {
    listDir.mockResolvedValue(fail("not_found", "文件或目录不存在"));
    const src = createLocalRootSource("r1", "project", "");
    await expect(src.listDir("")).rejects.toBeInstanceOf(LocalFsError);
    await expect(src.listDir("")).rejects.toMatchObject({
      code: "not_found",
    });
  });

  it("subpath source: out_of_root still throws (not treated as empty)", async () => {
    listDir.mockResolvedValue(fail("out_of_root", "路径越界，已拒绝"));
    const src = createLocalRootSource("r1", "chat", "ws");
    await expect(src.listDir("")).rejects.toMatchObject({
      code: "out_of_root",
    });
  });

  it("listDir hides GAgent/记忆 like trash", async () => {
    listDir.mockResolvedValue({
      ok: true,
      data: [
        {
          relPath: "GAgent/rules",
          name: "rules",
          kind: "dir",
          size: null,
          modifiedMs: 1,
        },
        {
          relPath: "GAgent/记忆",
          name: "记忆",
          kind: "dir",
          size: null,
          modifiedMs: 1,
        },
      ],
    });
    const src = createLocalRootSource("r1", "project", "");
    const kids = await src.listDir("GAgent");
    expect(kids.map((n) => n.path)).toEqual(["GAgent/rules"]);
  });
});

describe("createLocalRootSource listFileIndex", () => {
  let listDir: ReturnType<typeof vi.fn>;
  let listFiles: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    listDir = vi.fn();
    listFiles = vi.fn();
    (globalThis as unknown as { window: { fsApi: Partial<FsApi> } }).window = {
      fsApi: {
        listDir: listDir as FsApi["listDir"],
        listFiles: listFiles as FsApi["listFiles"],
      },
    };
  });

  it("requests recent order and passes files/truncated/mtime through", async () => {
    listFiles.mockResolvedValue({
      ok: true,
      data: {
        files: [
          { relPath: "a.ts", name: "a.ts", mtimeMs: 20 },
          { relPath: "b.ts", name: "b.ts", mtimeMs: 5 },
        ],
        truncated: true,
      },
    });
    const src = createLocalRootSource("r1", "project", "");
    const listFileIndex = src.listFileIndex;
    if (listFileIndex == null) throw new Error("expected listFileIndex");
    await expect(listFileIndex()).resolves.toEqual({
      files: [
        { relPath: "a.ts", mtimeMs: 20 },
        { relPath: "b.ts", mtimeMs: 5 },
      ],
      truncated: true,
    });
    expect(listFiles).toHaveBeenCalledWith("r1", { order: "recent" });
  });

  it("hides GAgent/记忆 from the file index", async () => {
    listFiles.mockResolvedValue({
      ok: true,
      data: {
        files: [
          { relPath: "GAgent/rules/a.md", name: "a.md", mtimeMs: 1 },
          { relPath: "GAgent/记忆", name: "记忆", mtimeMs: 2 },
          { relPath: "GAgent/记忆/画像.md", name: "画像.md", mtimeMs: 3 },
        ],
        truncated: false,
      },
    });
    const src = createLocalRootSource("r1", "project", "");
    const listFileIndex = src.listFileIndex;
    if (listFileIndex == null) throw new Error("expected listFileIndex");
    await expect(listFileIndex()).resolves.toEqual({
      files: [{ relPath: "GAgent/rules/a.md", mtimeMs: 1 }],
      truncated: false,
    });
  });
});

describe("createLocalRootSource delete uses OS trash", () => {
  let trashPath: ReturnType<typeof vi.fn>;
  let hardDelete: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    trashPath = vi.fn();
    hardDelete = vi.fn();
    (globalThis as unknown as { window: { fsApi: Partial<FsApi> } }).window = {
      fsApi: {
        trashPath: trashPath as FsApi["trashPath"],
        delete: hardDelete as FsApi["delete"],
      },
    };
  });

  it("calls trashPath, not fsApi.delete", async () => {
    trashPath.mockResolvedValue({ ok: true, data: undefined });
    const src = createLocalRootSource("r1", "project", "");
    await src.delete("notes.md");
    expect(trashPath).toHaveBeenCalledWith("r1", "notes.md");
    expect(hardDelete).not.toHaveBeenCalled();
  });

  it("prefixes subpath before trashPath", async () => {
    trashPath.mockResolvedValue({ ok: true, data: undefined });
    const src = createLocalRootSource("r1", "chat", "conversations/c1");
    await src.delete("a.md");
    expect(trashPath).toHaveBeenCalledWith("r1", "conversations/c1/a.md");
    expect(hardDelete).not.toHaveBeenCalled();
  });

  it("surfaces trashPath failure without falling back to hard delete", async () => {
    trashPath.mockResolvedValue(fail("error", "移入回收站失败"));
    const src = createLocalRootSource("r1", "project", "");
    await expect(src.delete("notes.md")).rejects.toMatchObject({
      name: "LocalFsError",
      code: "error",
      message: "移入回收站失败",
    });
    expect(hardDelete).not.toHaveBeenCalled();
  });
});
