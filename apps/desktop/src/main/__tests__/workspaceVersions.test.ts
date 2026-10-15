import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  app: { getPath: () => tmpdir() },
  dialog: {},
  ipcMain: { handle: vi.fn() },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
  shell: { trashItem: vi.fn(), showItemInFolder: vi.fn(), openPath: vi.fn() },
  clipboard: { writeText: vi.fn() },
}));

import { type StoredRoot, setRoot } from "../fs/roots";
import { listDir } from "../fs/tree";
import {
  deleteWorkspaceVersion,
  isValidVersionId,
  listWorkspaceVersions,
} from "../fs/workspaceVersions";

/**
 * 命名版本的**列举 / 删除**腿（创建 / 恢复走 sidecar，zip/unzip 只在 Python 侧）。
 * 盘上元数据由 Python 侧 `workspace/versions.py` 写入，这里按同一约定造盘再读。
 */

const META = (name: string, createdAt: string, sizeBytes: number): string =>
  JSON.stringify({
    version_id: "ignored-on-purpose",
    name,
    created_at: createdAt,
    size_bytes: sizeBytes,
  });

describe("workspace named versions (list / delete)", () => {
  let dir: string;
  let root: StoredRoot;

  const versionsDir = (subpath = ""): string =>
    subpath
      ? join(dir, subpath, "GAgent", "versions")
      : join(dir, "GAgent", "versions");

  const seedVersion = async (
    versionId: string,
    name: string,
    createdAt: string,
    subpath = "",
  ): Promise<void> => {
    const entry = join(versionsDir(subpath), versionId);
    await mkdir(entry, { recursive: true });
    await writeFile(join(entry, "content.zip"), "PK\u0003\u0004zip");
    await writeFile(join(entry, "meta.json"), META(name, createdAt, 8));
  };

  beforeEach(async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), "ws-versions-")));
    root = { id: "r-versions", name: "r", absPath: dir };
    setRoot(root);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await rm(dir, { recursive: true, force: true });
  });

  it("lists versions newest first with metadata read back", async () => {
    await seedVersion(
      "20260101T000000Z-aaaaaaaa",
      "第一版",
      "2026-01-01T00:00:00+00:00",
    );
    await seedVersion(
      "20260202T000000Z-bbbbbbbb",
      "第二版",
      "2026-02-02T00:00:00+00:00",
    );

    const res = await listWorkspaceVersions(root.id, "");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.map((v) => v.name)).toEqual(["第二版", "第一版"]);
    expect(res.data[0]).toEqual({
      versionId: "20260202T000000Z-bbbbbbbb",
      name: "第二版",
      createdAt: "2026-02-02T00:00:00+00:00",
      sizeBytes: 8,
    });
  });

  it("treats a missing versions zone as an empty list", async () => {
    const res = await listWorkspaceVersions(root.id, "");
    expect(res).toEqual({ ok: true, data: [] });
  });

  it("skips half-written and malformed entries", async () => {
    await seedVersion(
      "20260101T000000Z-aaaaaaaa",
      "好的",
      "2026-01-01T00:00:00+00:00",
    );
    // 只有 meta、没有 content.zip：恢复不了的还原点，不得列出
    const halfWritten = join(versionsDir(), "20260101T000000Z-cccccccc");
    await mkdir(halfWritten, { recursive: true });
    await writeFile(
      join(halfWritten, "meta.json"),
      META("半截", "2026-03-01T00:00:00+00:00", 1),
    );
    // meta 不是合法 json
    const broken = join(versionsDir(), "20260101T000000Z-dddddddd");
    await mkdir(broken, { recursive: true });
    await writeFile(join(broken, "content.zip"), "PK");
    await writeFile(join(broken, "meta.json"), "{ not json");

    const res = await listWorkspaceVersions(root.id, "");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.map((v) => v.versionId)).toEqual([
      "20260101T000000Z-aaaaaaaa",
    ]);
  });

  it("uses the directory name as the authoritative version id", async () => {
    await seedVersion(
      "20260101T000000Z-aaaaaaaa",
      "一",
      "2026-01-01T00:00:00+00:00",
    );
    const res = await listWorkspaceVersions(root.id, "");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    // meta 里写的是 "ignored-on-purpose"
    expect(res.data[0].versionId).toBe("20260101T000000Z-aaaaaaaa");
  });

  it("addresses a workspace under a container subpath", async () => {
    await seedVersion(
      "20260101T000000Z-aaaaaaaa",
      "子路径版",
      "2026-01-01T00:00:00+00:00",
      "conversations/c1",
    );
    const scoped = await listWorkspaceVersions(root.id, "conversations/c1");
    expect(scoped.ok).toBe(true);
    if (!scoped.ok) return;
    expect(scoped.data.map((v) => v.name)).toEqual(["子路径版"]);
    // 容器根自己没有版本区
    expect(await listWorkspaceVersions(root.id, "")).toEqual({
      ok: true,
      data: [],
    });
  });

  it("deletes one version and leaves the others", async () => {
    await seedVersion(
      "20260101T000000Z-aaaaaaaa",
      "一",
      "2026-01-01T00:00:00+00:00",
    );
    await seedVersion(
      "20260202T000000Z-bbbbbbbb",
      "二",
      "2026-02-02T00:00:00+00:00",
    );

    const del = await deleteWorkspaceVersion(
      root.id,
      "",
      "20260101T000000Z-aaaaaaaa",
    );
    expect(del.ok).toBe(true);

    const res = await listWorkspaceVersions(root.id, "");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.map((v) => v.versionId)).toEqual([
      "20260202T000000Z-bbbbbbbb",
    ]);
  });

  it("reports not_found when deleting an unknown version", async () => {
    const del = await deleteWorkspaceVersion(
      root.id,
      "",
      "20260101T000000Z-aaaaaaaa",
    );
    expect(del).toMatchObject({ ok: false, code: "not_found" });
  });

  it("rejects path-escaping version ids before touching disk", async () => {
    for (const bad of ["..", "../evil", "a/b", "a\\b", ""]) {
      expect(isValidVersionId(bad)).toBe(false);
      const del = await deleteWorkspaceVersion(root.id, "", bad);
      expect(del).toMatchObject({ ok: false, code: "invalid" });
    }
  });

  it("rejects a subpath that escapes the authorized root", async () => {
    const res = await listWorkspaceVersions(root.id, "../outside");
    expect(res).toMatchObject({ ok: false, code: "out_of_root" });
  });

  it("reports not_found for an unknown root", async () => {
    const res = await listWorkspaceVersions("no-such-root", "");
    expect(res).toMatchObject({ ok: false, code: "not_found" });
  });

  it("hides the versions zone from the user file tree", async () => {
    await seedVersion(
      "20260101T000000Z-aaaaaaaa",
      "一",
      "2026-01-01T00:00:00+00:00",
    );
    await mkdir(join(dir, "GAgent", "rules"), { recursive: true });

    const listed = await listDir(root.id, "GAgent");
    expect(listed.ok).toBe(true);
    if (!listed.ok) return;
    expect(listed.data.map((e) => e.name)).not.toContain("versions");
    expect(listed.data.map((e) => e.name)).toContain("rules");
  });
});
