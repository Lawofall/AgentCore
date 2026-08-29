// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  BASE_URL: "http://test",
  api: { get: vi.fn(), post: vi.fn() },
}));

// 只替真会出网/落盘的原语；wire→前端形状的纯映射保持真实（本文件正要断言它们的口径）。
vi.mock("@/services/workspaceHttp", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/workspaceHttp")>()),
  authedFetch: vi.fn(),
  saveBlob: vi.fn(),
}));

import { api } from "@/services/api";
import { authedFetch, saveBlob } from "@/services/workspaceHttp";
import {
  wsCreateSnapshot,
  wsDownloadArchive,
  wsExportZip,
  wsListSnapshots,
  wsListTrash,
  wsRestoreSnapshot,
  wsRestoreTrash,
} from "@/services/workspaces";

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);
const fetchMock = vi.mocked(authedFetch);
const saveMock = vi.mocked(saveBlob);

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  fetchMock.mockReset();
  saveMock.mockReset();
});

/**
 * 版本 / 软删区 / 导出的 **ws-id** 客户端 —— 「我的文件」拿到这三件能力的唯一通路
 * （文件页手里没有会话，走不了 conversation 版）。这里钉的是「打到哪个 URL、映射成
 * 什么形状」：ws id 形如 `folder:<uuid>`，冒号必须编码进路径段，否则整条路由就错了。
 */
describe("ws-id 版快照 / 软删区 / 导出", () => {
  it("列版本：编码 ws id，并映射成前端形状", async () => {
    apiGet.mockResolvedValue({
      data: [
        {
          snapshot_id: "snap-1",
          label: "改前留个版本",
          created_at: "2026-08-01T00:00:00Z",
          size_bytes: 2048,
        },
        {
          snapshot_id: "snap-0",
          label: null,
          created_at: "2026-07-31T00:00:00Z",
          size_bytes: 1024,
        },
      ],
      total: 2,
    });

    const out = await wsListSnapshots("folder:f1");

    expect(apiGet).toHaveBeenCalledWith("/v1/workspaces/folder%3Af1/snapshots");
    expect(out).toEqual([
      {
        snapshotId: "snap-1",
        label: "改前留个版本",
        createdAt: "2026-08-01T00:00:00Z",
        sizeBytes: 2048,
      },
      {
        snapshotId: "snap-0",
        label: null,
        createdAt: "2026-07-31T00:00:00Z",
        sizeBytes: 1024,
      },
    ]);
  });

  it("留版本：空白标签落成 null（= 自动备份口径），不是空字符串", async () => {
    apiPost.mockResolvedValue({
      snapshot_id: "snap-2",
      label: null,
      created_at: "2026-08-02T00:00:00Z",
      size_bytes: 10,
    });

    await wsCreateSnapshot("folder:f1", "   ");

    expect(apiPost).toHaveBeenCalledWith(
      "/v1/workspaces/folder%3Af1/snapshots",
      {
        label: null,
      },
    );
  });

  it("回滚打到该快照的 restore 路由", async () => {
    apiPost.mockResolvedValue({});

    await wsRestoreSnapshot("folder:f1", "snap-1");

    expect(apiPost).toHaveBeenCalledWith(
      "/v1/workspaces/folder%3Af1/snapshots/snap-1/restore",
    );
  });

  it("导出 ZIP = 先给当前文件打一份快照，再下载它", async () => {
    apiPost.mockResolvedValue({
      snapshot_id: "snap-9",
      label: "导出",
      created_at: "2026-08-03T00:00:00Z",
      size_bytes: 99,
    });
    fetchMock.mockResolvedValue({
      blob: async () => new Blob([new Uint8Array([1, 2])]),
    } as unknown as Response);

    await wsExportZip("folder:f1");

    expect(apiPost).toHaveBeenCalledWith(
      "/v1/workspaces/folder%3Af1/snapshots",
      {
        label: "导出",
      },
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://test/v1/workspaces/folder%3Af1/snapshots/snap-9/download",
    );
    expect(saveMock.mock.calls[0][1]).toBe("workspace-snap-9.zip");
  });

  it("文件树下载文件夹走 archive，不是 snapshots", async () => {
    fetchMock.mockResolvedValue({
      blob: async () => new Blob([new Uint8Array([1, 2])]),
    } as unknown as Response);

    await wsDownloadArchive("folder:f1", "docs/out", "out.zip");

    expect(apiPost).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://test/v1/workspaces/folder%3Af1/archive/docs/out",
    );
    expect(saveMock.mock.calls[0][1]).toBe("out.zip");
  });

  it("列软删区：带出服务端的保留天数（文案要照实说）", async () => {
    apiGet.mockResolvedValue({
      data: [
        {
          entry_id: "t1",
          original_path: "报告/终稿.md",
          name: "终稿.md",
          is_dir: false,
          deleted_at: "2026-08-04T00:00:00Z",
        },
      ],
      total: 1,
      retention_days: 30,
    });

    const out = await wsListTrash("folder:f1");

    expect(apiGet).toHaveBeenCalledWith("/v1/workspaces/folder%3Af1/trash");
    expect(out.retentionDays).toBe(30);
    expect(out.entries).toEqual([
      {
        entryId: "t1",
        originalPath: "报告/终稿.md",
        name: "终稿.md",
        isDir: false,
        deletedAt: "2026-08-04T00:00:00Z",
      },
    ]);
  });

  it("还原一条软删条目", async () => {
    apiPost.mockResolvedValue({});

    await wsRestoreTrash("folder:f1", "t1");

    expect(apiPost).toHaveBeenCalledWith(
      "/v1/workspaces/folder%3Af1/trash/t1/restore",
    );
  });
});
