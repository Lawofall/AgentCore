import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
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

import type { WorkspaceOpResult } from "@shared/ipc-contract";
import { shell } from "electron";
import { type StoredRoot, executeWorkspaceOp } from "../fs-service";
import { setRoot } from "../fs/roots";
import { trashPath } from "../fs/shell";
import { listDir } from "../fs/tree";
import {
  listWorkspaceTrash,
  restoreWorkspaceTrash,
} from "../fs/workspaceTrash";

const valOf = (r: WorkspaceOpResult): unknown => {
  if (!r.ok) throw new Error(`expected ok, got ${JSON.stringify(r.error)}`);
  return r.value;
};

describe("workspace listing hide system files", () => {
  let dir: string;
  let root: StoredRoot;

  beforeEach(async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), "ws-hide-")));
    root = { id: "r-hide", name: "r", absPath: dir };
    setRoot(root);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await rm(dir, { recursive: true, force: true });
  });

  it("index_files prunes GAgent/index, *.db, and media (AI tier)", async () => {
    await writeFile(join(dir, "notes.md"), "hi");
    await writeFile(join(dir, "hero.png"), "png");
    await mkdir(join(dir, "GAgent", "index"), { recursive: true });
    await writeFile(join(dir, "GAgent", "index", "cache.db"), "x");
    await mkdir(join(dir, "GAgent", "rules"), { recursive: true });
    await writeFile(join(dir, "GAgent", "rules", "r.md"), "r");
    await mkdir(join(dir, "index"), { recursive: true });
    await writeFile(join(dir, "index", "user.py"), "u");
    await writeFile(join(dir, "local.db"), "x");
    const res = valOf(await executeWorkspaceOp(root, "index_files", {})) as {
      paths: string[];
    };
    expect(res.paths).toEqual([
      "GAgent/rules/r.md",
      "index/user.py",
      "notes.md",
    ]);
  });

  it("listDir hides internal zones but keeps GAgent visible dirs and media", async () => {
    await writeFile(join(dir, "notes.md"), "hi");
    await writeFile(join(dir, "hero.png"), "png");
    await mkdir(join(dir, "GAgent", "index"), { recursive: true });
    await mkdir(join(dir, "GAgent", "rules"), { recursive: true });
    await writeFile(join(dir, "local.db"), "x");
    const listed = await listDir(root.id, "");
    expect(listed.ok).toBe(true);
    if (!listed.ok) return;
    expect(listed.data.map((e) => e.name).sort()).toEqual([
      "GAgent",
      "hero.png",
      "notes.md",
    ]);
    const acListed = await listDir(root.id, "GAgent");
    expect(acListed.ok).toBe(true);
    if (!acListed.ok) return;
    expect(acListed.data.map((e) => e.name).sort()).toEqual(["rules"]);
  });

  it("renames leftover GAgent/规则 to rules on listDir", async () => {
    await mkdir(join(dir, "GAgent", "规则"), { recursive: true });
    await writeFile(join(dir, "GAgent", "规则", "x.md"), "r");
    const listed = await listDir(root.id, "GAgent");
    expect(listed.ok).toBe(true);
    if (!listed.ok) return;
    expect(listed.data.map((e) => e.name)).toEqual(["rules"]);
    expect(
      await readFile(join(dir, "GAgent", "rules", "x.md"), "utf-8"),
    ).toBe("r");
  });
});

describe("trashPath soft-delete", () => {
  let dir: string;
  let root: StoredRoot;

  beforeEach(async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), "ws-trash-")));
    root = { id: "r-trash", name: "r", absPath: dir };
    setRoot(root);
    (shell.trashItem as ReturnType<typeof vi.fn>).mockReset();
    (shell.trashItem as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await rm(dir, { recursive: true, force: true });
  });

  it("moves a scratch subdir to the OS trash", async () => {
    const scratch = join(dir, "conversations", "c1");
    await mkdir(scratch, { recursive: true });
    await writeFile(join(scratch, "a.md"), "x");
    const res = await trashPath(root.id, "conversations/c1");
    expect(res.ok).toBe(true);
    expect(shell.trashItem).toHaveBeenCalledWith(scratch);
  });

  it("rejects trashing the root itself", async () => {
    const res = await trashPath(root.id, "");
    expect(res.ok).toBe(false);
    expect(shell.trashItem).not.toHaveBeenCalled();
  });

  it("treats a missing lazy scratch as success", async () => {
    const res = await trashPath(root.id, "conversations/missing");
    expect(res.ok).toBe(true);
    expect(shell.trashItem).not.toHaveBeenCalled();
  });

  it("reports trashItem failure without hard-deleting", async () => {
    const target = join(dir, "keep.md");
    await writeFile(target, "stay");
    (shell.trashItem as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("no recycle bin"),
    );
    const res = await trashPath(root.id, "keep.md");
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.reason).toContain("no recycle bin");
    }
    expect(await readFile(target, "utf-8")).toBe("stay");
  });
});

describe("GAgent/trash list + restore", () => {
  let dir: string;
  let root: StoredRoot;

  beforeEach(async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), "ws-ac-trash-")));
    root = { id: "r-ac-trash", name: "r", absPath: dir };
    setRoot(root);
    (shell.trashItem as ReturnType<typeof vi.fn>).mockReset();
    (shell.trashItem as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("no recycle bin"),
    );
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await rm(dir, { recursive: true, force: true });
  });

  it("lists and restores workspace-trash fallback entries", async () => {
    await writeFile(join(dir, "note.md"), "hello");
    const del = await executeWorkspaceOp(root, "delete", {
      path: "note.md",
      permanent: false,
    });
    expect(del.ok).toBe(true);
    expect(shell.trashItem).toHaveBeenCalled();

    const listed = await listWorkspaceTrash(root.id);
    expect(listed.ok).toBe(true);
    if (!listed.ok) return;
    expect(listed.data).toHaveLength(1);
    expect(listed.data[0].originalPath).toBe("note.md");

    const restored = await restoreWorkspaceTrash(
      root.id,
      listed.data[0].entryId,
    );
    expect(restored.ok).toBe(true);
    expect(await readFile(join(dir, "note.md"), "utf-8")).toBe("hello");
    const after = await listWorkspaceTrash(root.id);
    expect(after.ok && after.data).toEqual([]);
  });

  it("deletes GAgent by expanding children; rules restorable", async () => {
    await mkdir(join(dir, "GAgent", "rules"), { recursive: true });
    await writeFile(join(dir, "GAgent", "rules", "r.md"), "rule-body");
    await mkdir(join(dir, "GAgent", "index"), { recursive: true });
    await writeFile(join(dir, "GAgent", "index", "x.db"), "db");
    await mkdir(join(dir, "GAgent", "trash"), { recursive: true });

    const del = await executeWorkspaceOp(root, "delete", {
      path: "GAgent",
      permanent: false,
    });
    expect(del.ok).toBe(true);

    const listed = await listWorkspaceTrash(root.id);
    expect(listed.ok).toBe(true);
    if (!listed.ok) return;
    expect(listed.data.map((e) => e.originalPath)).toEqual(["GAgent/rules"]);

    const restored = await restoreWorkspaceTrash(
      root.id,
      listed.data[0].entryId,
    );
    expect(restored.ok).toBe(true);
    expect(
      await readFile(join(dir, "GAgent", "rules", "r.md"), "utf-8"),
    ).toBe("rule-body");
  });
});
