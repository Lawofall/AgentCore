/**
 * 单文件 directory 根（``rg PATTERN FILE``）须在合理时间内 settle——
 * 回归 a753a22f：``…/__init__.py`` 作 grep 根时本地通道拖到 ~59s 超时。
 */
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  app: {
    getPath: () => tmpdir(),
    getAppPath: () => tmpdir(),
    isPackaged: false,
  },
  dialog: {},
  ipcMain: { handle: vi.fn() },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
  shell: { trashItem: async () => undefined },
}));

import type { WorkspaceOpResult } from "@shared/ipc-contract";
import { type StoredRoot, executeWorkspaceOp } from "../fs-service";

const valOf = (r: WorkspaceOpResult): unknown => {
  if (!r.ok) throw new Error(`expected ok, got ${JSON.stringify(r.error)}`);
  return r.value;
};
const errOf = (r: WorkspaceOpResult) => {
  if (r.ok)
    throw new Error(`expected error, got value ${JSON.stringify(r.value)}`);
  return r.error;
};

function resolveTestRg(): string {
  const name = process.platform === "win32" ? "rg.exe" : "rg";
  const here = dirname(fileURLToPath(import.meta.url));
  const candidates = [
    process.env.AGENTCORE_RG_PATH,
    join(here, "..", "..", "..", "resources", "rg", name),
  ].filter((p): p is string => Boolean(p?.trim()));
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  throw new Error(
    "test rg binary missing; set AGENTCORE_RG_PATH or install via fetch_ripgrep.py",
  );
}

describe("opGrep single-file directory root", () => {
  let dir: string;
  let root: StoredRoot;
  let prevRg: string | undefined;

  beforeEach(async () => {
    prevRg = process.env.AGENTCORE_RG_PATH;
    process.env.AGENTCORE_RG_PATH = resolveTestRg();
    dir = await realpath(await mkdtemp(join(tmpdir(), "grep-sf-")));
    root = { id: "r", name: "r", absPath: dir };
    await mkdir(join(dir, "pkg"), { recursive: true });
    await writeFile(
      join(dir, "pkg", "__init__.py"),
      'needle = "StandingTask"\n# other\n',
      "utf8",
    );
    await writeFile(
      join(dir, "pkg", "sibling.py"),
      "needle = hidden\n",
      "utf8",
    );
  });

  afterEach(async () => {
    if (prevRg === undefined) process.env.AGENTCORE_RG_PATH = undefined;
    else process.env.AGENTCORE_RG_PATH = prevRg;
    await rm(dir, { recursive: true, force: true });
  });

  const grep = (args: Record<string, unknown>) =>
    executeWorkspaceOp(root, "grep", args);

  it("settles quickly for directory=__init__.py and scopes to that file", async () => {
    const t0 = Date.now();
    const r = await grep({
      pattern: "StandingTask",
      directory: "pkg/__init__.py",
    });
    const elapsed = Date.now() - t0;
    expect(elapsed).toBeLessThan(5_000);
    const value = valOf(r) as {
      hits: Array<{ path: string; line_no: number; text: string }>;
      total_matches: number;
    };
    expect(value.total_matches).toBeGreaterThanOrEqual(1);
    expect(value.hits.every((h) => h.path === "pkg/__init__.py")).toBe(true);
    expect(value.hits.some((h) => h.text.includes("StandingTask"))).toBe(true);
  });

  it("ignores glob when directory is a single file", async () => {
    const r = await grep({
      pattern: "StandingTask",
      directory: "pkg/__init__.py",
      glob: "*.ts",
    });
    const value = valOf(r) as { total_matches: number };
    expect(value.total_matches).toBeGreaterThanOrEqual(1);
  });

  it("returns a clear WorkspaceIOError for invalid regex on a file root", async () => {
    const t0 = Date.now();
    const r = await grep({
      pattern: "(unterminated",
      directory: "pkg/__init__.py",
    });
    expect(Date.now() - t0).toBeLessThan(5_000);
    const err = errOf(r);
    expect(err.kind).toBe("WorkspaceIOError");
    expect(String(err.detail)).toMatch(/正则/);
  });

  it("concurrent single-file greps all settle without hanging", async () => {
    const t0 = Date.now();
    const results = await Promise.all(
      Array.from({ length: 8 }, () =>
        grep({ pattern: "needle", directory: "pkg/__init__.py" }),
      ),
    );
    expect(Date.now() - t0).toBeLessThan(8_000);
    for (const r of results) {
      expect(r.ok).toBe(true);
      const value = valOf(r) as { total_matches: number };
      expect(value.total_matches).toBeGreaterThanOrEqual(1);
    }
  });
});
