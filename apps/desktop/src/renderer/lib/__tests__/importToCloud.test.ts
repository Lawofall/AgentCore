import {
  IMPORT_PUT_MAX_BYTES,
  ImportToCloudCancelledError,
  type ImportToCloudProgress,
  formatImportToCloudCancelledToast,
  formatImportToCloudProgress,
  formatImportToCloudToast,
  parseArchivePayload,
  runImportToCloud,
} from "@/lib/importToCloud";
import { useFoldersStore } from "@/stores/folders";
import JSZip from "jszip";
import { beforeEach, describe, expect, it, vi } from "vitest";

async function zipBase64(
  files: Record<string, Uint8Array | string>,
): Promise<string> {
  const zip = new JSZip();
  for (const [path, body] of Object.entries(files)) {
    zip.file(path, body);
  }
  return zip.generateAsync({ type: "base64" });
}

describe("parseArchivePayload", () => {
  it("accepts archive envelope", () => {
    expect(
      parseArchivePayload({
        archive: "abc",
        file_count: 2,
        total_bytes: 10,
        truncated: true,
      }),
    ).toEqual({
      archive: "abc",
      file_count: 2,
      total_bytes: 10,
      truncated: true,
    });
  });

  it("rejects non-objects", () => {
    expect(parseArchivePayload(null)).toBeNull();
    expect(parseArchivePayload("x")).toBeNull();
  });
});

describe("formatImportToCloudToast", () => {
  it("full success reminds continue in the new folder; session stays local", () => {
    const t = formatImportToCloudToast({
      folderId: "f1",
      folderName: "Demo",
      wsId: "folder:f1",
      uploaded: 3,
      skippedOversized: [],
      archiveTruncated: false,
      partial: false,
    });
    expect(t.message).toBe("已在「我的文件」建好「Demo」");
    expect(t.description).toContain("已上传 3 个文件");
    expect(t.description).toContain("请在新文件夹里继续");
    expect(t.description).toContain("当前对话用的还是本机原文件夹");
    // Size caps belong to the partial branch only, not the happy path.
    expect(t.description).not.toContain("MiB");
  });

  it("honest partial when truncated or oversized skipped", () => {
    const t = formatImportToCloudToast({
      folderId: "f1",
      folderName: "Big",
      wsId: "folder:f1",
      uploaded: 1,
      skippedOversized: ["huge.bin"],
      archiveTruncated: true,
      partial: true,
    });
    expect(t.message).toBe("已在「我的文件」建好「Big」（部分导入）");
    expect(t.description).toContain("100MiB");
    expect(t.description).toContain("50MiB");
    expect(t.description).toContain("已上传 1 个文件");
    expect(t.description).toContain("请在新文件夹里继续");
  });
});

describe("runImportToCloud", () => {
  beforeEach(() => {
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "quick_cloud" },
    });
  });

  it("creates cloud project, sets draft intent, uploads files", async () => {
    const archive = await zipBase64({
      "readme.md": "# hi",
      "src/a.ts": "export {}",
    });
    const uploadFile = vi.fn().mockResolvedValue(undefined);
    const createDir = vi.fn().mockResolvedValue(undefined);
    const removeRoot = vi.fn().mockResolvedValue(undefined);
    const addFolderToCache = vi.fn();
    const setDraftIntent = vi.fn((folderId: string) => {
      useFoldersStore.getState().setDraftWorkspaceIntent({
        kind: "folder",
        folderId,
      });
    });

    const result = await runImportToCloud({
      deps: {
        pickRoot: async () => ({
          ok: true,
          root: { id: "root-1", name: "my-app" },
        }),
        archiveRoot: async () => ({
          ok: true,
          value: {
            archive,
            file_count: 2,
            total_bytes: 20,
            truncated: false,
          },
        }),
        createCloudFolder: async (name) => ({
          folder: {
            id: "folder-99",
            name,
            mode: "cloud",
            localRootId: null,
            localSubpath: null,
          },
          created: true,
        }),
        uploadFile,
        createDir,
        removeRoot,
        setDraftIntent,
        addFolderToCache,
      },
    });

    expect(result.folderId).toBe("folder-99");
    expect(result.folderName).toBe("my-app");
    expect(result.wsId).toBe("folder:folder-99");
    expect(result.uploaded).toBe(2);
    expect(result.partial).toBe(false);
    expect(setDraftIntent).toHaveBeenCalledWith("folder-99");
    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "folder-99",
    });
    expect(addFolderToCache).toHaveBeenCalled();
    expect(uploadFile).toHaveBeenCalledTimes(2);
    expect(uploadFile.mock.calls.map((c) => c[1]).sort()).toEqual([
      "readme.md",
      "src/a.ts",
    ]);
    expect(removeRoot).toHaveBeenCalledWith("root-1");
  });

  it("skips oversized PUT leaves and marks partial", async () => {
    // Don't zip a 50MiB buffer — generate+inflate of that size times out the
    // default 5s vitest budget on Linux CI. The skip gate only reads byteLength.
    const loadAsync = vi.spyOn(JSZip, "loadAsync").mockResolvedValue({
      files: {
        "ok.txt": {
          dir: false,
          name: "ok.txt",
          async: async () => new TextEncoder().encode("small"),
        },
        "huge.bin": {
          dir: false,
          name: "huge.bin",
          async: async () =>
            ({ byteLength: IMPORT_PUT_MAX_BYTES + 1 }) as Uint8Array,
        },
      },
    } as unknown as JSZip);
    const uploadFile = vi.fn().mockResolvedValue(undefined);

    try {
      const result = await runImportToCloud({
        folderName: "Partial",
        deps: {
          pickRoot: async () => ({
            ok: true,
            root: { id: "r", name: "x" },
          }),
          archiveRoot: async () => ({
            ok: true,
            value: {
              archive: "unused",
              file_count: 2,
              total_bytes: IMPORT_PUT_MAX_BYTES + 6,
              truncated: false,
            },
          }),
          createCloudFolder: async (name) => ({
            folder: {
              id: "f",
              name,
              mode: "cloud",
              localRootId: null,
              localSubpath: null,
            },
            created: true,
          }),
          uploadFile,
          createDir: vi.fn(),
          removeRoot: vi.fn(),
          setDraftIntent: vi.fn(),
          addFolderToCache: vi.fn(),
        },
      });

      expect(result.uploaded).toBe(1);
      expect(result.skippedOversized).toEqual(["huge.bin"]);
      expect(result.partial).toBe(true);
      expect(uploadFile).toHaveBeenCalledTimes(1);
      expect(uploadFile.mock.calls[0]?.[1]).toBe("ok.txt");
    } finally {
      loadAsync.mockRestore();
    }
  });

  it("marks partial when archive truncated", async () => {
    const archive = await zipBase64({ "a.txt": "a" });
    const result = await runImportToCloud({
      root: { id: "kept", name: "kept" },
      deps: {
        pickRoot: async () => {
          throw new Error("should not pick when root given");
        },
        archiveRoot: async () => ({
          ok: true,
          value: {
            archive,
            file_count: 1,
            total_bytes: 1,
            truncated: true,
          },
        }),
        createCloudFolder: async () => ({
          folder: {
            id: "f2",
            name: "kept",
            mode: "cloud",
            localRootId: null,
            localSubpath: null,
          },
          created: true,
        }),
        uploadFile: vi.fn().mockResolvedValue(undefined),
        createDir: vi.fn(),
        removeRoot: vi.fn(),
        setDraftIntent: vi.fn(),
        addFolderToCache: vi.fn(),
      },
    });
    expect(result.archiveTruncated).toBe(true);
    expect(result.partial).toBe(true);
  });

  it("never creates mode=local folder", async () => {
    const createCloudFolder = vi.fn(async (name: string) => ({
      folder: {
        id: "c",
        name,
        mode: "cloud" as const,
        localRootId: null,
        localSubpath: null,
      },
      created: true,
    }));
    const archive = await zipBase64({ x: "1" });
    await runImportToCloud({
      deps: {
        pickRoot: async () => ({
          ok: true,
          root: { id: "r", name: "n" },
        }),
        archiveRoot: async () => ({
          ok: true,
          value: {
            archive,
            file_count: 1,
            total_bytes: 1,
            truncated: false,
          },
        }),
        createCloudFolder,
        uploadFile: vi.fn(),
        createDir: vi.fn(),
        removeRoot: vi.fn(),
        setDraftIntent: vi.fn(),
        addFolderToCache: vi.fn(),
      },
    });
    expect(createCloudFolder).toHaveBeenCalledWith("n");
  });

  it("reports progress phases including upload counts", async () => {
    const archive = await zipBase64({
      "a.txt": "a",
      "b.txt": "b",
    });
    const phases: ImportToCloudProgress[] = [];
    await runImportToCloud({
      root: { id: "r", name: "n" },
      deps: {
        pickRoot: async () => {
          throw new Error("no pick");
        },
        archiveRoot: async () => ({
          ok: true,
          value: {
            archive,
            file_count: 2,
            total_bytes: 2,
            truncated: false,
          },
        }),
        createCloudFolder: async (name) => ({
          folder: {
            id: "f",
            name,
            mode: "cloud",
            localRootId: null,
            localSubpath: null,
          },
          created: true,
        }),
        uploadFile: vi.fn().mockResolvedValue(undefined),
        createDir: vi.fn(),
        removeRoot: vi.fn(),
        setDraftIntent: vi.fn(),
        addFolderToCache: vi.fn(),
      },
      onProgress: (p) => phases.push(p),
    });
    expect(phases.some((p) => p.phase === "archiving")).toBe(true);
    expect(phases.some((p) => p.phase === "creating")).toBe(true);
    expect(
      phases
        .filter((p) => p.phase === "uploading")
        .map((p) => {
          if (p.phase !== "uploading") return null;
          return `${p.done}/${p.total}`;
        }),
    ).toEqual(["0/2", "1/2", "2/2"]);
    expect(phases.at(-1)).toEqual({ phase: "done" });
    expect(
      formatImportToCloudProgress({ phase: "uploading", done: 1, total: 2 }),
    ).toBe("上传中 1/2…");
  });

  it("aborts mid-upload, keeps folder id on cancelled error, still removes owned root", async () => {
    const archive = await zipBase64({
      "a.txt": "a",
      "b.txt": "b",
      "c.txt": "c",
    });
    const ac = new AbortController();
    const removeRoot = vi.fn().mockResolvedValue(undefined);
    let uploads = 0;
    const uploadFile = vi.fn().mockImplementation(async () => {
      uploads += 1;
      if (uploads === 1) ac.abort();
    });

    const err = await runImportToCloud({
      root: { id: "temp-root", name: "App" },
      ownsRoot: true,
      signal: ac.signal,
      deps: {
        pickRoot: async () => {
          throw new Error("no pick");
        },
        archiveRoot: async () => ({
          ok: true,
          value: {
            archive,
            file_count: 3,
            total_bytes: 3,
            truncated: false,
          },
        }),
        createCloudFolder: async (name) => ({
          folder: {
            id: "cloud-1",
            name,
            mode: "cloud",
            localRootId: null,
            localSubpath: null,
          },
          created: true,
        }),
        uploadFile,
        createDir: vi.fn(),
        removeRoot,
        setDraftIntent: vi.fn(),
        addFolderToCache: vi.fn(),
      },
    }).catch((e) => e);

    expect(err).toBeInstanceOf(ImportToCloudCancelledError);
    expect(err.folderId).toBe("cloud-1");
    expect(err.folderName).toBe("App");
    expect(uploadFile.mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(uploadFile.mock.calls.length).toBeLessThan(3);
    expect(removeRoot).toHaveBeenCalledWith("temp-root");
    const toast = formatImportToCloudCancelledToast(err);
    expect(toast.message).toContain("已保留");
    expect(toast.message).toContain("App");
  });

  it("aborts before create without folder metadata", async () => {
    const ac = new AbortController();
    ac.abort();
    const err = await runImportToCloud({
      root: { id: "r", name: "n" },
      ownsRoot: true,
      signal: ac.signal,
      deps: {
        pickRoot: async () => {
          throw new Error("no pick");
        },
        archiveRoot: async () => {
          throw new Error("should not archive");
        },
        createCloudFolder: async () => {
          throw new Error("should not create");
        },
        uploadFile: vi.fn(),
        createDir: vi.fn(),
        removeRoot: vi.fn(),
        setDraftIntent: vi.fn(),
        addFolderToCache: vi.fn(),
      },
    }).catch((e) => e);

    expect(err).toBeInstanceOf(ImportToCloudCancelledError);
    expect(err.folderId).toBeUndefined();
    expect(formatImportToCloudCancelledToast(err).message).toBe("已取消导入");
  });

  it("does not removeRoot when ownsRoot is false", async () => {
    const archive = await zipBase64({ "a.txt": "a" });
    const removeRoot = vi.fn();
    await runImportToCloud({
      root: { id: "shared", name: "shared" },
      ownsRoot: false,
      deps: {
        pickRoot: async () => {
          throw new Error("no pick");
        },
        archiveRoot: async () => ({
          ok: true,
          value: {
            archive,
            file_count: 1,
            total_bytes: 1,
            truncated: false,
          },
        }),
        createCloudFolder: async () => ({
          folder: {
            id: "f",
            name: "shared",
            mode: "cloud",
            localRootId: null,
            localSubpath: null,
          },
          created: true,
        }),
        uploadFile: vi.fn().mockResolvedValue(undefined),
        createDir: vi.fn(),
        removeRoot,
        setDraftIntent: vi.fn(),
        addFolderToCache: vi.fn(),
      },
    });
    expect(removeRoot).not.toHaveBeenCalled();
  });
});
