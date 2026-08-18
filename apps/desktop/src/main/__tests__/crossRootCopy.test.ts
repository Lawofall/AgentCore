/**
 * Workspace → organize-mount copy (split-root). Reverse / move stay denied.
 * @vitest-environment node
 */
import {
  access,
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
  ipcMain: { handle: vi.fn(), on: vi.fn() },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
  shell: { trashItem: vi.fn(), showItemInFolder: vi.fn(), openPath: vi.fn() },
  clipboard: { writeText: vi.fn() },
}));

vi.mock("../log-service", () => ({ logDesktop: vi.fn() }));

import type { StoredRoot } from "../fs/roots";
import { __test as rootsTest } from "../fs/roots";
import { executeWorkspaceOp } from "../fs/workspace/dispatch";
import {
  crossRootCopyError,
  crossRootMoveError,
  splitRootCopyError,
  splitRootMoveError,
} from "../fs/workspace/sessionRoot";

async function pathExists(p: string): Promise<boolean> {
  try {
    await access(p);
    return true;
  } catch {
    return false;
  }
}

const wsRoot = (absPath: string): StoredRoot => ({
  id: "primary",
  name: "ws",
  absPath,
});

const orgRoot = (absPath: string): StoredRoot => ({
  id: "ext-out",
  name: "out",
  absPath,
  sessionOnly: true,
  conversationId: "c1",
  mode: "organize",
  alias: "out",
});

const roRoot = (absPath: string): StoredRoot => ({
  id: "ext-ro",
  name: "ro",
  absPath,
  sessionOnly: true,
  conversationId: "c1",
  mode: "readonly",
  alias: "ro",
});

describe("cross-root copy / move policy", () => {
  const ws = wsRoot("C:\\ws");
  const org = orgRoot("C:\\out");
  const other = { ...org, id: "ext-b", alias: "b" };

  it("allows in-root and workspace → external copy; denies reverse and cross-mount", () => {
    expect(crossRootCopyError(ws, ws)).toBeNull();
    expect(crossRootCopyError(org, org)).toBeNull();
    expect(crossRootCopyError(ws, org)).toBeNull();
    expect(crossRootCopyError(org, ws)).toContain("工作区");
    expect(crossRootCopyError(org, other)).toContain("授权目录复制");
  });

  it("never allows cross-root move", () => {
    expect(crossRootMoveError(ws, ws)).toBeNull();
    expect(crossRootMoveError(org, org)).toBeNull();
    expect(crossRootMoveError(ws, org)).toContain("工作区");
    expect(crossRootMoveError(org, ws)).toContain("工作区");
    expect(crossRootMoveError(org, other)).toContain("授权目录移动");
  });

  it("split-root copy is workspace → organize only", () => {
    expect(splitRootCopyError(ws, ws)).toBeNull();
    expect(splitRootCopyError(ws, org)).toBeNull();
    expect(splitRootCopyError(org, ws)).toContain("工作区");
    expect(splitRootCopyError(ws, roRoot("C:\\ro"))).toContain("工作区");
    const otherWs: StoredRoot = { id: "other-ws", name: "b", absPath: "C:\\b" };
    expect(splitRootCopyError(ws, otherWs)).toContain("工作区");
  });

  it("split-root move denies any id mismatch", () => {
    expect(splitRootMoveError(ws, ws)).toBeNull();
    expect(splitRootMoveError(ws, org)).toContain("工作区");
  });
});

describe("executeWorkspaceOp split-root copy", () => {
  let parent: string;
  let wsDir: string;
  let extDir: string;
  let ws: StoredRoot;
  let org: StoredRoot;
  let ro: StoredRoot;

  beforeEach(async () => {
    parent = await realpath(await mkdtemp(join(tmpdir(), "ws-xcopy-")));
    wsDir = join(parent, "ws");
    extDir = join(parent, "ext");
    await mkdir(wsDir);
    await mkdir(extDir);
    ws = wsRoot(wsDir);
    org = orgRoot(extDir);
    ro = roRoot(extDir);
    rootsTest.reset(
      new Map<string, StoredRoot>([
        [ws.id, ws],
        [org.id, org],
        [ro.id, ro],
      ]),
    );
  });

  afterEach(async () => {
    rootsTest.reset();
    await rm(parent, { recursive: true, force: true });
  });

  it("copies workspace → organize without removing the source", async () => {
    await writeFile(join(wsDir, "report.md"), "hello");
    const r = await executeWorkspaceOp(org, "copy", {
      src: "report.md",
      dst: "report.md",
      src_root_id: ws.id,
    });
    expect(r.ok).toBe(true);
    expect(await readFile(join(wsDir, "report.md"), "utf-8")).toBe("hello");
    expect(await readFile(join(extDir, "report.md"), "utf-8")).toBe("hello");
  });

  it("refuses to overwrite an existing dest", async () => {
    await writeFile(join(wsDir, "report.md"), "new");
    await writeFile(join(extDir, "report.md"), "old");
    const r = await executeWorkspaceOp(org, "copy", {
      src: "report.md",
      dst: "report.md",
      src_root_id: ws.id,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.kind).toBe("AlreadyExists");
    expect(await readFile(join(extDir, "report.md"), "utf-8")).toBe("old");
    expect(await readFile(join(wsDir, "report.md"), "utf-8")).toBe("new");
  });

  it("refuses workspace → readonly", async () => {
    await writeFile(join(wsDir, "report.md"), "hello");
    const r = await executeWorkspaceOp(ro, "copy", {
      src: "report.md",
      dst: "report.md",
      src_root_id: ws.id,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("OutsideWorkspace");
      expect(r.error.detail).toMatch(/只读|不能写入/);
    }
    expect(await pathExists(join(extDir, "report.md"))).toBe(false);
  });

  it("refuses organize → workspace reverse copy", async () => {
    await writeFile(join(extDir, "report.md"), "hello");
    const r = await executeWorkspaceOp(ws, "copy", {
      src: "report.md",
      dst: "report.md",
      src_root_id: org.id,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("OutsideWorkspace");
      expect(r.error.detail).toBe("不能跨会话授权目录与工作区复制文件");
    }
    expect(await pathExists(join(wsDir, "report.md"))).toBe(false);
    expect(await readFile(join(extDir, "report.md"), "utf-8")).toBe("hello");
  });

  it("refuses cross-root move", async () => {
    await writeFile(join(wsDir, "report.md"), "hello");
    const r = await executeWorkspaceOp(org, "move", {
      src: "report.md",
      dst: "report.md",
      src_root_id: ws.id,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("OutsideWorkspace");
      expect(r.error.detail).toBe("不能跨会话授权目录与工作区移动文件");
    }
    expect(await readFile(join(wsDir, "report.md"), "utf-8")).toBe("hello");
    expect(await pathExists(join(extDir, "report.md"))).toBe(false);
  });

  it("keeps dest pathGuard on the organize root", async () => {
    await writeFile(join(wsDir, "report.md"), "hello");
    const secret = join(parent, "secret.txt");
    const r = await executeWorkspaceOp(org, "copy", {
      src: "report.md",
      dst: "../secret.txt",
      src_root_id: ws.id,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.kind).toBe("OutsideWorkspace");
    expect(await pathExists(secret)).toBe(false);
  });

  it("copy with src_data writes dest and does not look up src on dest root", async () => {
    const r = await executeWorkspaceOp(org, "copy", {
      dst: "report.md",
      src_data: Buffer.from("hello", "utf8").toString("base64"),
    });
    expect(r.ok).toBe(true);
    expect(await readFile(join(extDir, "report.md"), "utf-8")).toBe("hello");
    expect(await pathExists(join(wsDir, "report.md"))).toBe(false);
  });

  it("copy with src_data refuses overwrite", async () => {
    await writeFile(join(extDir, "report.md"), "old");
    const r = await executeWorkspaceOp(org, "copy", {
      dst: "report.md",
      src_data: Buffer.from("new", "utf8").toString("base64"),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.kind).toBe("AlreadyExists");
    expect(await readFile(join(extDir, "report.md"), "utf-8")).toBe("old");
  });

  it("copy with src_data refuses readonly dest", async () => {
    const r = await executeWorkspaceOp(ro, "copy", {
      dst: "report.md",
      src_data: Buffer.from("hello", "utf8").toString("base64"),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("OutsideWorkspace");
      expect(r.error.detail).toMatch(/只读|不能写入/);
    }
  });
});
