import {
  get as getBorrow,
  isBorrowActive,
} from "@/lib/borrowOriginalPreference";
import {
  formatBorrowToCloudToast,
  startBorrowToCloudJob,
} from "@/lib/borrowToCloudJob";
// @vitest-environment jsdom
import { ImportToCloudCancelledError } from "@/lib/importToCloud";
import { getMergeLanding } from "@/lib/mergeLandingPreference";
import { openDraftConversation } from "@/lib/newConversation";
import {
  __clearMemoryUiStorageForTests,
  __setUiStorageBackendForTests,
} from "@/lib/uiStorage";
import { useFoldersStore } from "@/stores/folders";
import { useImportToCloudJobStore } from "@/stores/importToCloudJob";
import type { FsRoot } from "@shared/ipc-contract";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  }),
}));

vi.mock("@/lib/importToCloud", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/importToCloud")>();
  return {
    ...actual,
    runImportToCloud: vi.fn(),
  };
});

vi.mock("@/lib/newConversation", () => ({
  openDraftConversation: vi.fn(),
}));

vi.mock("@/lib/queryClient", () => ({
  queryClient: { invalidateQueries: vi.fn() },
}));

vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
}));

const root = { id: "root-1", name: "MyApp" } as FsRoot;
const memory = new Map<string, string>();

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

const IMPORT_CONTINUE = "请在新文件夹里继续";
const IMPORT_STAY_LOCAL = "当前对话用的还是本机原文件夹";

describe("formatBorrowToCloudToast", () => {
  it("says copied to cloud and original unchanged; not the import continue hint", () => {
    const t = formatBorrowToCloudToast({
      folderId: "f1",
      folderName: "Demo",
      wsId: "folder:f1",
      uploaded: 3,
      skippedOversized: [],
      archiveTruncated: false,
      partial: false,
    });
    expect(t.message).toBe("已复制到云上「Demo」");
    expect(t.description).toContain("电脑上的原件还没改");
    expect(t.description).not.toContain(IMPORT_CONTINUE);
    expect(t.description).not.toContain(IMPORT_STAY_LOCAL);
    expect(`${t.message}${t.description}`).not.toMatch(
      /合回|过桥|遗留|云协作|本机传统|sidecar|通道/,
    );
  });
});

describe("startBorrowToCloudJob", () => {
  beforeEach(() => {
    memory.clear();
    __setUiStorageBackendForTests({
      getItem: (key) => memory.get(key) ?? null,
      setItem: (key, value) => {
        memory.set(key, value);
      },
      removeItem: (key) => {
        memory.delete(key);
      },
      keys: () => [...memory.keys()],
    });
    useImportToCloudJobStore.setState({
      running: false,
      controller: null,
    });
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "quick_cloud" },
    });
    vi.clearAllMocks();
  });

  afterEach(() => {
    __setUiStorageBackendForTests(null);
    __clearMemoryUiStorageForTests();
  });

  it("shares the job store: refuses when import is already running", async () => {
    const held = new AbortController();
    expect(useImportToCloudJobStore.getState().begin(held)).toBe(true);
    expect(
      startBorrowToCloudJob({
        root,
        folderName: "Demo",
      }),
    ).toBe(false);
    const { runImportToCloud } = await import("@/lib/importToCloud");
    expect(runImportToCloud).not.toHaveBeenCalled();
  });

  it("uploads with ownsRoot false, writes landing + borrow mark, opens the cloud folder draft", async () => {
    const { runImportToCloud } = await import("@/lib/importToCloud");
    vi.mocked(runImportToCloud).mockResolvedValue({
      folderId: "f-cloud",
      folderName: "Demo",
      wsId: "folder:f-cloud",
      uploaded: 2,
      skippedOversized: [],
      archiveTruncated: false,
      partial: false,
    });

    expect(
      startBorrowToCloudJob({
        root,
        folderName: "Demo",
      }),
    ).toBe(true);
    await flush();

    expect(runImportToCloud).toHaveBeenCalledWith(
      expect.objectContaining({
        root,
        ownsRoot: false,
        folderName: "Demo",
      }),
    );
    expect(getMergeLanding({ kind: "folder", folderId: "f-cloud" })).toEqual({
      rootId: "root-1",
    });
    expect(getBorrow("f-cloud")).toEqual({
      rootId: "root-1",
      originalName: "MyApp",
      promoted: false,
    });
    expect(isBorrowActive("f-cloud")).toBe(true);
    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "f-cloud",
    });
    expect(openDraftConversation).toHaveBeenCalledWith("f-cloud");
    expect(toast.success).toHaveBeenCalledWith(
      "已复制到云上「Demo」",
      expect.objectContaining({
        description: expect.stringContaining("电脑上的原件还没改"),
      }),
    );
    const successCall = vi.mocked(toast.success).mock.calls[0];
    const desc =
      (successCall?.[1] as { description?: string })?.description ?? "";
    expect(desc).not.toContain(IMPORT_CONTINUE);
    expect(desc).not.toContain(IMPORT_STAY_LOCAL);
  });

  it("does not write borrow mark or open draft when cancelled before a folder exists", async () => {
    const { runImportToCloud } = await import("@/lib/importToCloud");
    vi.mocked(runImportToCloud).mockRejectedValue(
      new ImportToCloudCancelledError(),
    );
    startBorrowToCloudJob({
      root,
      folderName: "Demo",
    });
    await flush();
    expect(getBorrow("f-cloud")).toBeNull();
    expect(openDraftConversation).not.toHaveBeenCalled();
  });

  it("keeps landing + borrow mark when cancelled after the cloud folder exists", async () => {
    const { runImportToCloud } = await import("@/lib/importToCloud");
    vi.mocked(runImportToCloud).mockRejectedValue(
      new ImportToCloudCancelledError({
        folderId: "f-keep",
        folderName: "Keep",
      }),
    );
    startBorrowToCloudJob({
      root,
      folderName: "Keep",
    });
    await flush();
    expect(getMergeLanding({ kind: "folder", folderId: "f-keep" })).toEqual({
      rootId: "root-1",
    });
    expect(isBorrowActive("f-keep")).toBe(true);
    expect(openDraftConversation).toHaveBeenCalledWith("f-keep");
  });
});
