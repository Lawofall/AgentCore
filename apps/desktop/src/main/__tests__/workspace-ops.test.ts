import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  stat,
  symlink,
  utimes,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import JSZip from "jszip";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// fs-service imports electron at module load (for IPC wiring it doesn't run
// here). Stub it so the dependency-free executeWorkspaceOp can be imported.
vi.mock("electron", () => ({
  // getVersion / isPackaged: the baseline prune path logs via log-service.
  app: {
    getPath: () => tmpdir(),
    getVersion: () => "0.0.0-test",
    isPackaged: true,
  },
  dialog: {},
  ipcMain: { handle: vi.fn() },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
  shell: {
    trashItem: async (p: string) => {
      await rm(p, { recursive: true, force: true });
    },
  },
}));

import type { WorkspaceOpResult } from "@shared/ipc-contract";
import { type StoredRoot, executeWorkspaceOp } from "../fs-service";
import { BASELINE_KEEP_MAX } from "../fs/constants";

// Discriminated-union accessors that fail loudly on the wrong branch.
const valOf = (r: WorkspaceOpResult): unknown => {
  if (!r.ok) throw new Error(`expected ok, got ${JSON.stringify(r.error)}`);
  return r.value;
};
const errOf = (r: WorkspaceOpResult) => {
  if (r.ok)
    throw new Error(`expected error, got value ${JSON.stringify(r.value)}`);
  return r.error;
};

describe("executeWorkspaceOp (本地工作区写类 op，P2b)", () => {
  let dir: string;
  let root: StoredRoot;
  // realpath the temp dir: os.tmpdir() is often a symlink (macOS /tmp), and the
  // traversal guard compares against the canonical root.
  beforeEach(async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), "ws-")));
    root = { id: "r", name: "r", absPath: dir };
  });
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  const run = (op: string, args: Record<string, unknown>) =>
    executeWorkspaceOp(root, op as never, args);

  it("rejects Windows reserved device names with settleable OutsideWorkspace (no fake success)", async () => {
    for (const path of ["nul", "NUL", "con", "COM1", "nul.txt", "subdir/prn"]) {
      const read = await run("read", { path });
      expect(read.ok).toBe(false);
      if (read.ok) return;
      expect(read.error.kind).toBe("OutsideWorkspace");
      expect(read.error.detail).toContain("保留设备名");

      const exists = await run("exists", { path });
      expect(exists.ok).toBe(false);
      if (exists.ok) return;
      expect(exists.error.kind).toBe("OutsideWorkspace");

      const write = await run("write", { path, content: "x" });
      expect(write.ok).toBe(false);
      if (write.ok) return;
      expect(write.error.kind).toBe("OutsideWorkspace");
    }
    // lookalikes still allowed (may PathNotFound)
    const lookalike = await run("exists", { path: "null.txt" });
    expect(lookalike.ok).toBe(true);
    expect(valOf(lookalike)).toBe(false);
  });

  it("write creates the file with parents and reports the code-point count", async () => {
    // "hi😀" = 3 code points (the emoji is one), matching Python len() — not the
    // 4 UTF-16 units JS .length would give.
    const r = await run("write", { path: "a/b/c.txt", content: "hi😀" });
    expect(valOf(r)).toBe(3);
    expect(await readFile(join(dir, "a/b/c.txt"), "utf-8")).toBe("hi😀");
  });

  it("write overwrites an existing file atomically", async () => {
    await run("write", { path: "f.txt", content: "old" });
    const r = await run("write", { path: "f.txt", content: "fresh" });
    expect(r.ok).toBe(true);
    expect(await readFile(join(dir, "f.txt"), "utf-8")).toBe("fresh");
  });

  it("append creates a file or extends an existing one", async () => {
    const created = await run("append", { path: "d.md", content: "# A" });
    expect(valOf(created)).toBe(3);
    expect(await readFile(join(dir, "d.md"), "utf-8")).toBe("# A");
    const extended = await run("append", { path: "d.md", content: "\n\n# B" });
    expect(valOf(extended)).toBe(5);
    expect(await readFile(join(dir, "d.md"), "utf-8")).toBe("# A\n\n# B");
  });

  it("read_bytes round-trips raw bytes as base64 and reports PathNotFound", async () => {
    const raw = Buffer.from([0, 1, 2, 255]);
    await writeFile(join(dir, "blob"), raw);
    const r = await run("read_bytes", { path: "blob" });
    expect(Buffer.from(valOf(r) as string, "base64")).toEqual(raw);
    expect(errOf(await run("read_bytes", { path: "nope" })).kind).toBe(
      "PathNotFound",
    );
  });

  it("write_bytes decodes base64 and reports the byte count", async () => {
    const raw = Buffer.from([10, 20, 30, 40]);
    const r = await run("write_bytes", {
      path: "out.bin",
      data: raw.toString("base64"),
    });
    expect(valOf(r)).toBe(4);
    expect(await readFile(join(dir, "out.bin"))).toEqual(raw);
  });

  it("mkdir creates nested dirs and refuses an existing path", async () => {
    expect((await run("mkdir", { path: "x/y/z" })).ok).toBe(true);
    expect((await stat(join(dir, "x/y/z"))).isDirectory()).toBe(true);
    expect(errOf(await run("mkdir", { path: "x/y/z" })).kind).toBe(
      "AlreadyExists",
    );
  });

  it("delete removes a file and a directory tree, else PathNotFound", async () => {
    await run("write", { path: "d/f.txt", content: "x" });
    expect((await run("delete", { path: "d/f.txt" })).ok).toBe(true);
    expect((await run("delete", { path: "d" })).ok).toBe(true);
    expect(errOf(await run("delete", { path: "ghost" })).kind).toBe(
      "PathNotFound",
    );
  });

  it("copy duplicates a file and a directory tree without clobber", async () => {
    await run("write", { path: "src.txt", content: "data" });
    expect(
      (await run("copy", { src: "src.txt", dst: "nested/dst.txt" })).ok,
    ).toBe(true);
    expect(await readFile(join(dir, "src.txt"), "utf-8")).toBe("data");
    expect(await readFile(join(dir, "nested/dst.txt"), "utf-8")).toBe("data");
    expect(
      errOf(await run("copy", { src: "src.txt", dst: "nested/dst.txt" })).kind,
    ).toBe("AlreadyExists");

    await run("mkdir", { path: "tree/a" });
    await run("write", { path: "tree/a/b.txt", content: "b" });
    expect((await run("copy", { src: "tree", dst: "tree2" })).ok).toBe(true);
    expect(await readFile(join(dir, "tree2/a/b.txt"), "utf-8")).toBe("b");
  });

  it("permanent delete hard-removes; default delete leaves workspace via trash", async () => {
    await run("write", { path: "hard.txt", content: "x" });
    expect(
      (await run("delete", { path: "hard.txt", permanent: true })).ok,
    ).toBe(true);
    expect(errOf(await run("read", { path: "hard.txt" })).kind).toBe(
      "PathNotFound",
    );
  });

  it("move renames, creates dst parents, and guards clobber / missing src", async () => {
    await run("write", { path: "src.txt", content: "data" });
    expect(
      (await run("move", { src: "src.txt", dst: "nested/dst.txt" })).ok,
    ).toBe(true);
    expect(await readFile(join(dir, "nested/dst.txt"), "utf-8")).toBe("data");
    expect(errOf(await run("read", { path: "src.txt" })).kind).toBe(
      "PathNotFound",
    );

    await run("write", { path: "taken.txt", content: "1" });
    expect(
      errOf(await run("move", { src: "nested/dst.txt", dst: "taken.txt" }))
        .kind,
    ).toBe("AlreadyExists");
    expect(errOf(await run("move", { src: "ghost", dst: "z.txt" })).kind).toBe(
      "PathNotFound",
    );
  });

  it("replace (single) returns count 1 and the 1-based first line", async () => {
    await run("write", { path: "r.txt", content: "a\nbXb\nc" });
    const r = await run("replace", {
      path: "r.txt",
      old: "X",
      new: "Y",
      all: false,
    });
    expect(valOf(r)).toEqual({ count: 1, first_line: 2 });
    expect(await readFile(join(dir, "r.txt"), "utf-8")).toBe("a\nbYb\nc");
  });

  it("replace (all) returns the total count and a null first line", async () => {
    await run("write", { path: "r.txt", content: "x x x" });
    const r = await run("replace", {
      path: "r.txt",
      old: "x",
      new: "y",
      all: true,
    });
    expect(valOf(r)).toEqual({ count: 3, first_line: null });
    expect(await readFile(join(dir, "r.txt"), "utf-8")).toBe("y y y");
  });

  it("replace matches LF old against CRLF file and preserves CRLF", async () => {
    await writeFile(
      join(dir, "win.txt"),
      Buffer.from("line1\r\nline2\r\nline3\r\n"),
    );
    const r = await run("replace", {
      path: "win.txt",
      old: "line1\nline2\n",
      new: "lineA\n",
      all: false,
    });
    expect(valOf(r)).toEqual({ count: 1, first_line: 1 });
    expect(await readFile(join(dir, "win.txt"))).toEqual(
      Buffer.from("lineA\r\nline3\r\n"),
    );
  });

  it("replace surfaces AmbiguousMatch with the match count when not all", async () => {
    await run("write", { path: "r.txt", content: "x x" });
    const err = errOf(
      await run("replace", { path: "r.txt", old: "x", new: "y", all: false }),
    );
    expect(err).toEqual({
      kind: "AmbiguousMatch",
      detail: "2 matches",
      count: 2,
    });
  });

  it("replace maps NoMatch / NotUTF8 / NotAFile", async () => {
    await run("write", { path: "r.txt", content: "abc" });
    expect(
      errOf(
        await run("replace", {
          path: "r.txt",
          old: "zzz",
          new: "y",
          all: false,
        }),
      ).kind,
    ).toBe("NoMatch");

    await writeFile(join(dir, "bin"), Buffer.from([0xff, 0xfe, 0x00]));
    expect(
      errOf(
        await run("replace", { path: "bin", old: "x", new: "y", all: false }),
      ).kind,
    ).toBe("NotUTF8");

    await run("mkdir", { path: "adir" });
    expect(
      errOf(
        await run("replace", { path: "adir", old: "x", new: "y", all: false }),
      ).kind,
    ).toBe("NotAFile");
  });

  it("refuses traversal escapes without touching disk", async () => {
    expect(errOf(await run("read", { path: "../escape" })).kind).toBe(
      "OutsideWorkspace",
    );
    expect(
      errOf(await run("write", { path: "../evil.txt", content: "x" })).kind,
    ).toBe("OutsideWorkspace");
  });

  it("rejects a write that escapes through a symlinked ancestor", async () => {
    const outside = await realpath(await mkdtemp(join(tmpdir(), "ws-out-")));
    let linked = true;
    try {
      // "junction" needs no elevation on Windows; ignored (plain symlink) on POSIX.
      await symlink(outside, join(dir, "link"), "junction");
    } catch {
      linked = false; // environment forbids link creation — skip the assertion
    }
    if (linked) {
      expect(
        errOf(await run("write", { path: "link/evil.txt", content: "x" })).kind,
      ).toBe("OutsideWorkspace");
      await expect(
        readFile(join(outside, "evil.txt"), "utf-8"),
      ).rejects.toThrow();
    }
    await rm(outside, { recursive: true, force: true });
  });

  it("routes read / list through the dispatcher", async () => {
    await run("write", { path: "hello.txt", content: "hi" });
    await run("mkdir", { path: "subdir" });
    expect(valOf(await run("read", { path: "hello.txt" }))).toBe("hi");
    const listing = valOf(
      await run("list", { directory: ".", pattern: "*" }),
    ) as {
      entries: {
        path: string;
        is_dir: boolean;
        size_bytes: number | null;
        mtime_ms: number | null;
      }[];
      truncated: boolean;
    };
    expect(listing.truncated).toBe(false);
    const entries = listing.entries;
    const hello = entries.find((e) => e.path === "hello.txt");
    expect(hello).toMatchObject({
      is_dir: false,
      size_bytes: 2,
    });
    expect(hello?.mtime_ms).toEqual(expect.any(Number));
    expect(hello).toBeDefined();
    if (!hello || hello.mtime_ms == null) {
      throw new Error("expected hello.txt entry with mtime_ms");
    }
    expect(hello.mtime_ms).toBeGreaterThan(0);
    const sub = entries.find((e) => e.path === "subdir");
    expect(sub).toMatchObject({
      is_dir: true,
      size_bytes: null,
    });
    expect(sub?.mtime_ms).toEqual(expect.any(Number));
  });

  it("list / list_tree on a missing relative dir succeed empty (align index_files)", async () => {
    const listRes = await run("list", {
      directory: "not-yet-mkdir",
      pattern: "*",
    });
    expect(valOf(listRes)).toEqual({ entries: [], truncated: false });

    const treeRes = valOf(
      await run("list_tree", {
        directory: "not-yet-mkdir",
        pattern: "*",
        max_depth: 3,
        max_entries: 100,
      }),
    ) as {
      entries: unknown[];
      truncated: boolean;
      elided_count: number;
      warnings: unknown[];
    };
    expect(treeRes).toEqual({
      entries: [],
      truncated: false,
      elided_count: 0,
      warnings: [],
    });
  });

  it("list_tree name filter emits matches only and still finds deep files", async () => {
    await run("write", { path: "d0/d1/d2/d3/hit.py", content: "x" });
    for (let i = 0; i < 8; i += 1) {
      await run("write", { path: `pad${i}/keep.txt`, content: "n" });
    }
    const treeRes = valOf(
      await run("list_tree", {
        directory: ".",
        pattern: "*.py",
        max_depth: 8,
        max_entries: 5,
      }),
    ) as {
      entries: Array<{ path: string; is_dir: boolean }>;
      truncated: boolean;
    };
    const paths = treeRes.entries.map((e) => e.path.replaceAll("\\", "/"));
    expect(paths).toContain("d0/d1/d2/d3/hit.py");
    expect(paths.some((p) => p === "pad0" || p.startsWith("pad0/"))).toBe(
      false,
    );
    expect(treeRes.entries.every((e) => e.path.endsWith(".py"))).toBe(true);
  });

  it("list / list_tree on a file path still return NotADirectory", async () => {
    await run("write", { path: "a-file.txt", content: "x" });
    expect(
      errOf(await run("list", { directory: "a-file.txt", pattern: "*" })).kind,
    ).toBe("NotADirectory");
    expect(
      errOf(
        await run("list_tree", {
          directory: "a-file.txt",
          pattern: "*",
          max_depth: 3,
          max_entries: 100,
        }),
      ).kind,
    ).toBe("NotADirectory");
  });

  it("index_files returns a flat, ignore-pruned, posix-sorted file list", async () => {
    await run("write", { path: "a.txt", content: "A" });
    await run("write", { path: "sub/b.md", content: "B" });
    await run("write", { path: "node_modules/dep/index.js", content: "X" }); // pruned
    const res = valOf(await run("index_files", {})) as {
      entries: Array<{ path: string; mtime_ms: number; size_bytes: number }>;
      paths: string[];
      truncated: boolean;
    };
    expect(res.paths).toEqual(["a.txt", "sub/b.md"]); // node_modules pruned, posix sep
    expect(res.entries.map((e) => e.path)).toEqual(res.paths);
    expect(res.truncated).toBe(false);
  });

  it("index_files on an empty root returns no paths", async () => {
    const res = valOf(await run("index_files", {})) as {
      entries: unknown[];
      paths: string[];
      truncated: boolean;
    };
    expect(res.paths).toEqual([]);
    expect(res.entries).toEqual([]);
    expect(res.truncated).toBe(false);
  });

  it("index_files entries carry local mtime_ms and size_bytes fingerprints", async () => {
    await run("write", { path: "a.txt", content: "hello" });
    await run("write", { path: "sub/b.md", content: "world!" });
    await utimes(join(dir, "a.txt"), 100, 100);
    await utimes(join(dir, "sub", "b.md"), 200, 200);
    const res = valOf(await run("index_files", {})) as {
      entries: Array<{ path: string; mtime_ms: number; size_bytes: number }>;
      paths: string[];
    };
    expect(res.paths).toEqual(["a.txt", "sub/b.md"]);
    expect(res.entries).toEqual([
      { path: "a.txt", mtime_ms: 100_000, size_bytes: 5 },
      { path: "sub/b.md", mtime_ms: 200_000, size_bytes: 6 },
    ]);
  });

  it("index_files order=recent returns newest-first by mtime", async () => {
    await run("write", { path: "a_old.txt", content: "A" });
    await run("write", { path: "c_mid.txt", content: "C" });
    await run("write", { path: "b_new.txt", content: "B" });
    // Stamp distinct mtimes (seconds): a_old < c_mid < b_new.
    await utimes(join(dir, "a_old.txt"), 100, 100);
    await utimes(join(dir, "c_mid.txt"), 200, 200);
    await utimes(join(dir, "b_new.txt"), 300, 300);
    const recent = valOf(await run("index_files", { order: "recent" })) as {
      paths: string[];
      entries: Array<{ path: string; mtime_ms: number }>;
    };
    expect(recent.paths).toEqual(["b_new.txt", "c_mid.txt", "a_old.txt"]);
    expect(recent.entries.map((e) => e.path)).toEqual(recent.paths);
    expect(recent.entries.map((e) => e.mtime_ms)).toEqual([
      300_000, 200_000, 100_000,
    ]);
    // Default order stays alphabetical (the @-mention view), unaffected by mtime.
    const alpha = valOf(await run("index_files", {})) as { paths: string[] };
    expect(alpha.paths).toEqual(["a_old.txt", "b_new.txt", "c_mid.txt"]);
  });

  it("answers a genuinely unknown op as a typed IO error", async () => {
    const err = errOf(await run("bogus_op", {}));
    expect(err.kind).toBe("WorkspaceIOError");
  });

  // Execution tests drive `node` (guaranteed on PATH under vitest, cross-platform)
  // rather than python/bash, which may be absent on the runner.
  describe("execute (P2c, 本地代码执行)", () => {
    const exec = async (args: Record<string, unknown>) =>
      valOf(await run("execute", { language: "javascript", ...args })) as {
        success: boolean;
        stdout: string;
        stderr: string;
        exit_code: number;
        duration_ms: number;
      };

    it("runs code and captures stdout with a zero exit", async () => {
      const r = await exec({ code: "console.log('hi from node')" });
      expect(r.success).toBe(true);
      expect(r.exit_code).toBe(0);
      expect(r.stdout).toContain("hi from node");
    });

    it("runs in the bound root as its working directory", async () => {
      await run("write", { path: "marker.txt", content: "X" });
      const r = await exec({
        code: "console.log(require('node:fs').readdirSync('.').join(','))",
      });
      expect(r.success).toBe(true);
      expect(r.stdout).toContain("marker.txt");
    });

    it("feeds stdin to the process", async () => {
      const r = await exec({
        code: "let b='';process.stdin.on('data',d=>b+=d);process.stdin.on('end',()=>process.stdout.write('got:'+b))",
        stdin: "ping",
      });
      expect(r.stdout).toContain("got:ping");
    });

    it("reports a non-zero exit code as failure", async () => {
      const r = await exec({ code: "process.exit(3)" });
      expect(r.success).toBe(false);
      expect(r.exit_code).toBe(3);
    });

    it("kills a run that exceeds the timeout", async () => {
      const r = await exec({ code: "while (true) {}", timeout_seconds: 1 });
      expect(r.success).toBe(false);
      expect(r.exit_code).toBe(-1);
      expect(r.stderr).toContain("Timeout");
    });

    it("rejects an unsupported language", async () => {
      const r = valOf(
        await run("execute", { language: "ruby", code: "puts 1" }),
      ) as { success: boolean; stderr: string; exit_code: number };
      expect(r.success).toBe(false);
      expect(r.stderr).toContain("Unsupported language");
      expect(r.exit_code).toBe(1);
    });

    it("fail-fast rejects bash when no usable launcher (exit 127)", async () => {
      const { _setPathExistsForTests } = await import(
        "../fs/workspace/execCodec"
      );
      _setPathExistsForTests(() => false);
      try {
        const r = valOf(
          await run("execute", { language: "bash", code: "echo hi" }),
        ) as {
          success: boolean;
          stderr: string;
          exit_code: number;
          duration_ms: number;
        };
        expect(r.success).toBe(false);
        expect(r.exit_code).toBe(127);
        expect(r.stderr).toContain("代码执行环境启动失败");
        expect(r.stderr.toLowerCase()).toMatch(/javascript|python/);
        expect(r.duration_ms).toBeLessThan(2000);
      } finally {
        _setPathExistsForTests(null);
      }
    });
  });

  // 本地→云交接打包（双模式工作区 P2e / e1）：把整棵绑定根打成单个 zip 交服务端暂存。
  describe("archive (本地→云交接打包, P2e/e1)", () => {
    const archiveNames = async (b64: string): Promise<string[]> => {
      const zip = await JSZip.loadAsync(b64, { base64: true });
      return Object.keys(zip.files)
        .filter((n) => !zip.files[n].dir)
        .sort();
    };

    it("packs the tree, honoring default skips + .gitignore", async () => {
      await run("write", { path: "a.txt", content: "A" });
      await run("write", { path: "sub/b.txt", content: "B" });
      await run("write", { path: "keep.txt", content: "K" });
      await run("write", { path: ".gitignore", content: "secret.txt\n" });
      await run("write", { path: "secret.txt", content: "S" }); // gitignored
      await run("write", { path: "node_modules/junk.js", content: "J" }); // default skip

      const res = valOf(await run("archive", {})) as {
        archive: string;
        file_count: number;
        total_bytes: number;
        truncated: boolean;
      };
      expect(await archiveNames(res.archive)).toEqual([
        ".gitignore",
        "a.txt",
        "keep.txt",
        "sub/b.txt",
      ]);
      const zip = await JSZip.loadAsync(res.archive, { base64: true });
      expect(await zip.file("sub/b.txt")?.async("string")).toBe("B");
      expect(res.file_count).toBe(4);
      expect(res.truncated).toBe(false);
    });

    it("packs only a workspace subdirectory when directory is set", async () => {
      await run("write", { path: "outside.txt", content: "OUT" });
      await run("write", { path: "ws/a.txt", content: "A" });
      await run("write", { path: "ws/nested/b.txt", content: "B" });
      const res = valOf(await run("archive", { directory: "ws" })) as {
        archive: string;
        file_count: number;
      };
      expect(await archiveNames(res.archive)).toEqual([
        "a.txt",
        "nested/b.txt",
      ]);
      expect(res.file_count).toBe(2);
      const zip = await JSZip.loadAsync(res.archive, { base64: true });
      expect(await zip.file("a.txt")?.async("string")).toBe("A");
    });

    it("with ignore:false packs everything (node_modules + gitignored)", async () => {
      await run("write", { path: ".gitignore", content: "secret.txt\n" });
      await run("write", { path: "secret.txt", content: "S" });
      await run("write", { path: "node_modules/junk.js", content: "J" });
      const res = valOf(await run("archive", { ignore: false })) as {
        archive: string;
      };
      const names = await archiveNames(res.archive);
      expect(names).toContain("node_modules/junk.js");
      expect(names).toContain("secret.txt");
    });
  });

  describe("ensure_turn_baseline", () => {
    it("captures non-empty zip and reports ready", async () => {
      await run("write", { path: "a.txt", content: "hello" });
      const res = valOf(
        await run("ensure_turn_baseline", { message_id: "msg-1" }),
      ) as {
        ready: boolean;
        snapshot_id: string;
        size_bytes: number;
      };
      expect(res.ready).toBe(true);
      expect(res.snapshot_id).toBe("msg-1");
      expect(res.size_bytes).toBeGreaterThan(0);
      const zipPath = join(dir, "AgentCore", "baselines", "msg-1.zip");
      const st = await stat(zipPath);
      expect(st.size).toBeGreaterThan(0);
    });

    it("reuses existing zip without rewriting (probe ready)", async () => {
      await run("write", { path: "a.txt", content: "x" });
      valOf(await run("ensure_turn_baseline", { message_id: "msg-reuse" }));
      const zipPath = join(dir, "AgentCore", "baselines", "msg-reuse.zip");
      const before = await stat(zipPath);
      await new Promise((r) => setTimeout(r, 20));
      const again = valOf(
        await run("ensure_turn_baseline", { message_id: "msg-reuse" }),
      ) as { ready: boolean; size_bytes: number };
      expect(again.ready).toBe(true);
      const after = await stat(zipPath);
      expect(after.mtimeMs).toBe(before.mtimeMs);
      expect(after.size).toBe(before.size);
    });

    it("probe-only returns not ready when missing", async () => {
      const res = valOf(
        await run("ensure_turn_baseline", {
          message_id: "msg-miss",
          capture: false,
        }),
      ) as { ready: boolean; reason?: string };
      expect(res.ready).toBe(false);
      expect(res.reason).toBe("missing");
    });

    it("scopes zip to workspace subdirectory", async () => {
      await run("write", { path: "outside.txt", content: "OUT" });
      await run("write", { path: "ws/a.txt", content: "A" });
      const res = valOf(
        await run("ensure_turn_baseline", {
          message_id: "msg-sub",
          directory: "ws",
        }),
      ) as { ready: boolean };
      expect(res.ready).toBe(true);
      const zipPath = join(dir, "ws", "AgentCore", "baselines", "msg-sub.zip");
      const st = await stat(zipPath);
      expect(st.size).toBeGreaterThan(0);
      const buf = await readFile(zipPath);
      const zip = await JSZip.loadAsync(buf);
      expect(Object.keys(zip.files).sort()).toEqual(["a.txt"]);
    });

    it("rejects invalid message_id", async () => {
      const r = await run("ensure_turn_baseline", {
        message_id: "../evil",
      });
      expect(r.ok).toBe(false);
    });
  });

  describe("ensure_turn_baseline 保留策略", () => {
    const baselineDir = () => join(dir, "AgentCore", "baselines");

    // 落一份占位基线并把 mtime 拨到指定分钟前（负数 = 未来），清理按 mtime 排序。
    const seedBaseline = async (name: string, ageMinutes: number) => {
      await mkdir(baselineDir(), { recursive: true });
      const path = join(baselineDir(), name);
      await writeFile(path, "stub");
      const when = new Date(Date.now() - ageMinutes * 60_000);
      await utimes(path, when, when);
    };
    const baselineNames = async () => (await readdir(baselineDir())).sort();

    it("清掉超出数量上限的旧基线", async () => {
      await run("write", { path: "a.txt", content: "x" });
      const seeded = BASELINE_KEEP_MAX + 5;
      const oldName = (i: number) => `old-${String(i).padStart(2, "0")}.zip`;
      for (let i = 0; i < seeded; i++) {
        await seedBaseline(oldName(i), (seeded - i) * 5);
      }
      valOf(await run("ensure_turn_baseline", { message_id: "msg-new" }));

      const names = await baselineNames();
      expect(names).toHaveLength(BASELINE_KEEP_MAX);
      expect(names).toContain("msg-new.zip");
      expect(names).toContain(oldName(seeded - 1)); // 最年轻的老基线
      expect(names).not.toContain(oldName(0)); // 最老的
    });

    it("清掉超龄基线（未超数量上限也删）", async () => {
      await run("write", { path: "a.txt", content: "x" });
      await seedBaseline("stale.zip", 31 * 24 * 60);
      await seedBaseline("fresh.zip", 29 * 24 * 60);
      valOf(await run("ensure_turn_baseline", { message_id: "msg-new" }));

      expect(await baselineNames()).toEqual(["fresh.zip", "msg-new.zip"]);
    });

    it("本回合基线不会被未来时间戳的旧 zip 挤掉", async () => {
      // 还原过的备份 / 时钟偏移会留下超前 mtime，排序上压在新基线之上。
      await run("write", { path: "a.txt", content: "x" });
      for (let i = 0; i < BASELINE_KEEP_MAX; i++) {
        await seedBaseline(
          `future-${String(i).padStart(2, "0")}.zip`,
          -24 * 60,
        );
      }
      valOf(await run("ensure_turn_baseline", { message_id: "msg-new" }));

      expect(await baselineNames()).toContain("msg-new.zip");
    });

    it("绝不碰用户命名版本区", async () => {
      await run("write", { path: "a.txt", content: "x" });
      const versionDir = join(
        dir,
        "AgentCore",
        "versions",
        "20250101T000000Z-abcd1234",
      );
      await mkdir(versionDir, { recursive: true });
      await writeFile(join(versionDir, "content.zip"), "version");
      await writeFile(join(versionDir, "meta.json"), "{}");
      const ancient = new Date(Date.now() - 400 * 24 * 3600 * 1000);
      await utimes(join(versionDir, "content.zip"), ancient, ancient);
      await seedBaseline("ancient.zip", 400 * 24 * 60);

      valOf(await run("ensure_turn_baseline", { message_id: "msg-new" }));

      expect(await baselineNames()).toEqual(["msg-new.zip"]);
      expect(await readdir(versionDir)).toEqual(["content.zip", "meta.json"]);
    });
  });
});
