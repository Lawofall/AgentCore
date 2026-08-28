/**
 * W3 session grant root gates: mode whitelist / conversation ownership /
 * reversible delete (pathGuard algorithm unchanged).
 * @vitest-environment node
 */
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
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

import type { WorkspaceOpName } from "@shared/ipc-contract";
import { shell } from "electron";
import type { StoredRoot } from "../fs/roots";
import { executeWorkspaceOp } from "../fs/workspace/dispatch";
import {
  buildExternalEnvFromRoots,
  buildWorkspacePythonpathEnv,
  pickRegistryEnv,
  pickUserExecEnv,
} from "../fs/workspace/exec";
import {
  ORGANIZE_ALLOWED_OPS,
  ORGANIZE_DENIED_OPS,
  ORGANIZE_MUTATION_OPS,
  READONLY_ALLOWED_OPS,
  sessionRootAccessError,
} from "../fs/workspace/sessionRoot";

const readonlyRoot: StoredRoot = {
  id: "s1",
  name: "reports",
  absPath: "C:\\tmp\\reports",
  sessionOnly: true,
  conversationId: "c1",
  mode: "readonly",
  alias: "reports",
};

describe("session readonly root write refusal", () => {
  it("rejects file write ops on readonly roots", async () => {
    const r = await executeWorkspaceOp(readonlyRoot, "write", {
      path: "a.txt",
      content: "x",
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("OutsideWorkspace");
      expect(r.error.detail).toContain("只读");
    }
  });

  it.each([
    "execute",
    "process_start",
    "archive",
    "ensure_turn_baseline",
  ] as const)("rejects %s on readonly roots", async (op) => {
    const r = await executeWorkspaceOp(readonlyRoot, op, {});
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("OutsideWorkspace");
      expect(r.error.detail).toContain("只读");
    }
  });
});

describe("session organize root mode whitelist", () => {
  const organizeRoot: StoredRoot = {
    id: "s2",
    name: "Desktop",
    absPath: "C:\\tmp\\desktop",
    sessionOnly: true,
    conversationId: "c1",
    mode: "organize",
    alias: "desktop",
  };

  it("mode gate allows move/copy/mkdir/delete under organize", async () => {
    for (const op of ["mkdir", "move", "copy", "delete"] as const) {
      expect(
        sessionRootAccessError(
          organizeRoot,
          op,
          op === "mkdir" || op === "delete"
            ? { path: "Docs" }
            : { src: "a.txt", dst: "Docs/a.txt" },
        ),
      ).toBeNull();
    }
  });

  it.each([
    "write",
    "execute",
    "process_start",
    "archive",
    "ensure_turn_baseline",
  ] as const)("rejects %s under organize", async (op) => {
    const r = await executeWorkspaceOp(organizeRoot, op, {
      path: "a.txt",
      content: "x",
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("OutsideWorkspace");
      expect(r.error.detail).toMatch(/整理授权|不允许/);
    }
  });

  it("rejects permanent delete under organize", async () => {
    const r = await executeWorkspaceOp(organizeRoot, "delete", {
      path: "a.txt",
      permanent: true,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.detail).toContain("永久删除");
    }
  });
});

/**
 * 穷尽 `WorkspaceOpName` 的策略表：新增 op 不在表里 → 类型检查红；
 * 表与集合不一致 → 本用例红。两端对齐（服务端 `external_mounts.py`）另由
 * `apps/server/tests/test_external_op_parity.py` 断言。
 */
const OP_POLICY: Record<WorkspaceOpName, "readonly" | "mutation" | "denied"> = {
  read: "readonly",
  read_bytes: "readonly",
  read_head: "readonly",
  read_lines: "readonly",
  list: "readonly",
  exists: "readonly",
  list_tree: "readonly",
  index_files: "readonly",
  grep: "readonly",
  diagnostics: "readonly",
  probe_exec: "readonly",
  process_read: "readonly",
  process_list: "readonly",
  process_stop: "readonly",
  git_repo_status: "readonly",
  move: "mutation",
  copy: "mutation",
  mkdir: "mutation",
  delete: "mutation",
  write: "denied",
  append: "denied",
  write_bytes: "denied",
  replace: "denied",
  execute: "denied",
  process_start: "denied",
  archive: "denied",
  ensure_turn_baseline: "denied",
  git_scm: "denied",
  git_run: "denied",
};

describe("session root op whitelist is exhaustive over WorkspaceOpName", () => {
  const readonlyOnly: StoredRoot = {
    id: "s-ro",
    name: "reports",
    absPath: "C:\\tmp\\reports",
    sessionOnly: true,
    conversationId: "c1",
    mode: "readonly",
  };
  const organizeOnly: StoredRoot = {
    ...readonlyOnly,
    id: "s-org",
    mode: "organize",
  };
  const ops = Object.keys(OP_POLICY) as WorkspaceOpName[];

  it("classifies every op exactly once, with no stray set members", () => {
    for (const op of ops) {
      const policy = OP_POLICY[op];
      expect([
        READONLY_ALLOWED_OPS.has(op),
        ORGANIZE_MUTATION_OPS.has(op),
        ORGANIZE_DENIED_OPS.has(op),
      ]).toEqual([
        policy === "readonly",
        policy === "mutation",
        policy === "denied",
      ]);
    }
    const classified =
      READONLY_ALLOWED_OPS.size +
      ORGANIZE_MUTATION_OPS.size +
      ORGANIZE_DENIED_OPS.size;
    expect(classified).toBe(ops.length);
    expect(ORGANIZE_ALLOWED_OPS.size).toBe(
      READONLY_ALLOWED_OPS.size + ORGANIZE_MUTATION_OPS.size,
    );
  });

  it("gates each op per mode straight from the table", () => {
    for (const op of ops) {
      const policy = OP_POLICY[op];
      expect([
        sessionRootAccessError(readonlyOnly, op, {}) === null,
        sessionRootAccessError(organizeOnly, op, {}) === null,
      ]).toEqual([policy === "readonly", policy !== "denied"]);
    }
  });

  it("attach_rw allows write/replace/execute; permanent delete still denied", () => {
    const attach: StoredRoot = { ...readonlyOnly, id: "s-rw", mode: "attach_rw" };
    for (const op of ops) {
      expect(sessionRootAccessError(attach, op, {})).toBeNull();
    }
    expect(
      sessionRootAccessError(attach, "delete", { permanent: true })?.ok,
    ).toBe(false);
    const future = "teleport" as WorkspaceOpName;
    expect(sessionRootAccessError(attach, future, {})?.ok).toBe(false);
  });

  it("denies an unclassified op instead of falling through (whitelist, not blacklist)", () => {
    const future = "teleport" as WorkspaceOpName;
    expect(sessionRootAccessError(readonlyOnly, future, {})?.ok).toBe(false);
    expect(sessionRootAccessError(organizeOnly, future, {})?.ok).toBe(false);
  });

  it("leaves permanent (non-session) roots ungated", () => {
    const permanent: StoredRoot = { id: "p1", name: "proj", absPath: "C:\\p" };
    for (const op of ops) {
      expect(sessionRootAccessError(permanent, op, {})).toBeNull();
    }
  });
});

describe("session grant delete stays reversible", () => {
  let dir: string;
  let grantRoot: StoredRoot;
  const trashItem = vi.mocked(shell.trashItem);

  beforeEach(async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), "ws-grant-del-")));
    grantRoot = {
      id: "s-del",
      name: "Documents",
      absPath: dir,
      sessionOnly: true,
      conversationId: "c1",
      mode: "organize",
      alias: "documents",
    };
    trashItem.mockReset();
    trashItem.mockResolvedValue(undefined);
  });

  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  // 区外根下的 AgentCore/{index,trash,baselines} 是用户自己的东西（well-known
  // Documents 授权 + 默认容器根即 Documents/AgentCore）——普通 delete 不得 rm -rf。
  it.each(["AgentCore/trash", "AgentCore/index", "AgentCore/baselines"])(
    "sends %s to the system trash instead of rm -rf",
    async (relPath) => {
      const zone = join(dir, ...relPath.split("/"));
      await mkdir(zone, { recursive: true });
      await writeFile(join(zone, "keep.txt"), "user data");

      const r = await executeWorkspaceOp(grantRoot, "delete", {
        path: relPath,
      });
      expect(r.ok).toBe(true);
      expect(trashItem).toHaveBeenCalledWith(zone);
      // 回收站被 mock（不真搬走）→ 内容仍在，证明没有走 fs.rm(recursive)。
      expect(await readFile(join(zone, "keep.txt"), "utf-8")).toBe("user data");
    },
  );

  it("ignores permanent=true and still routes to the system trash", async () => {
    await writeFile(join(dir, "note.md"), "hello");
    const r = await executeWorkspaceOp(grantRoot, "delete", {
      path: "note.md",
      permanent: true,
    });
    // dispatch 门先拒；即便直调 op 层也只走可逆路径。
    expect(r.ok).toBe(false);
    const direct = await import("../fs/workspace/write");
    expect(await direct.opDelete(grantRoot, "note.md", true)).toEqual({
      ok: true,
      value: null,
    });
    expect(trashItem).toHaveBeenCalledWith(join(dir, "note.md"));
    expect(await readFile(join(dir, "note.md"), "utf-8")).toBe("hello");
  });

  it("fails honestly when the system trash is unavailable (no AgentCore/trash on the user disk)", async () => {
    trashItem.mockRejectedValue(new Error("no recycle bin"));
    await writeFile(join(dir, "note.md"), "hello");

    const r = await executeWorkspaceOp(grantRoot, "delete", {
      path: "note.md",
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.kind).toBe("WorkspaceIOError");
      expect(r.error.detail).toContain("系统回收站不可用");
    }
    expect(await readFile(join(dir, "note.md"), "utf-8")).toBe("hello");
    expect(await readdir(dir)).toEqual(["note.md"]);
  });

  it("keeps hard-delete of internal zones on the product's own workspace root", async () => {
    const zone = join(dir, "AgentCore", "index");
    await mkdir(zone, { recursive: true });
    await writeFile(join(zone, "code_search.db"), "x");
    const workspaceRoot: StoredRoot = {
      id: "p-del",
      name: "proj",
      absPath: dir,
    };

    const r = await executeWorkspaceOp(workspaceRoot, "delete", {
      path: "AgentCore/index",
    });
    expect(r.ok).toBe(true);
    expect(trashItem).not.toHaveBeenCalled();
    expect(await readdir(join(dir, "AgentCore"))).toEqual([]);
  });
});

describe("buildExternalEnvFromRoots conversation ownership", () => {
  const grant: StoredRoot = {
    id: "ext-1",
    name: "reports",
    absPath: "C:\\data\\reports",
    sessionOnly: true,
    conversationId: "c1",
    mode: "readonly",
    alias: "reports",
  };
  const otherConv: StoredRoot = {
    ...grant,
    id: "ext-2",
    conversationId: "c-other",
  };
  const permanent: StoredRoot = {
    id: "perm-1",
    name: "proj",
    absPath: "C:\\data\\proj",
  };

  const lookup = (id: string) =>
    ({ "ext-1": grant, "ext-2": otherConv, "perm-1": permanent })[id];

  it("injects only matching sessionOnly grants for the conversation", () => {
    const env = buildExternalEnvFromRoots(
      { reports: "ext-1", other: "ext-2", proj: "perm-1" },
      "c1",
      lookup,
    );
    expect(env).toEqual({ AGENTCORE_EXTERNAL_REPORTS: "C:\\data\\reports" });
  });

  it("skips organize-mode session roots from env injection", () => {
    const organize: StoredRoot = {
      ...grant,
      id: "ext-org",
      mode: "organize",
    };
    const orgLookup = (id: string) =>
      ({ "ext-1": grant, "ext-org": organize })[id];
    const env = buildExternalEnvFromRoots(
      { reports: "ext-1", desk: "ext-org" },
      "c1",
      orgLookup,
    );
    expect(env).toEqual({ AGENTCORE_EXTERNAL_REPORTS: "C:\\data\\reports" });
  });

  it("skips injection when conversation_id is empty", () => {
    const env = buildExternalEnvFromRoots({ reports: "ext-1" }, "", lookup);
    expect(env).toEqual({});
  });
});

describe("buildWorkspacePythonpathEnv (D11′)", () => {
  it("prepends cwd and existing src/lib, keeps previous PYTHONPATH", async () => {
    const { mkdtemp, mkdir, rm } = await import("node:fs/promises");
    const { tmpdir } = await import("node:os");
    const { join, delimiter } = await import("node:path");
    const root = await mkdtemp(join(tmpdir(), "ac-pp-"));
    try {
      await mkdir(join(root, "src"));
      const env = buildWorkspacePythonpathEnv(root, "keep-me");
      const parts = env.PYTHONPATH.split(delimiter);
      expect(parts[0]).toBe(root);
      expect(parts).toContain(join(root, "src"));
      expect(parts[parts.length - 1]).toBe("keep-me");
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});

describe("pickRegistryEnv", () => {
  it("keeps registry/cache keys and drops arbitrary env", () => {
    expect(
      pickRegistryEnv({
        NPM_CONFIG_REGISTRY: "https://registry.npmjs.org/",
        npm_config_cache: "/tmp/npm",
        YARN_REGISTRY: "https://registry.npmjs.org/",
        PNPM_STORE_PATH: "/tmp/pnpm",
        PIP_INDEX_URL: "https://pypi.org/simple/",
        UV_CACHE_DIR: "/tmp/uv",
        POETRY_CACHE_DIR: "/tmp/poetry",
        PATH: "/evil",
        LD_PRELOAD: "x",
        SECRET: "no",
      }),
    ).toEqual({
      NPM_CONFIG_REGISTRY: "https://registry.npmjs.org/",
      npm_config_cache: "/tmp/npm",
      YARN_REGISTRY: "https://registry.npmjs.org/",
      PNPM_STORE_PATH: "/tmp/pnpm",
      PIP_INDEX_URL: "https://pypi.org/simple/",
      UV_CACHE_DIR: "/tmp/uv",
      POETRY_CACHE_DIR: "/tmp/poetry",
    });
  });

  it("returns empty for non-objects", () => {
    expect(pickRegistryEnv(null)).toEqual({});
    expect(pickRegistryEnv("x")).toEqual({});
    expect(pickRegistryEnv(["NPM_CONFIG_REGISTRY"])).toEqual({});
  });
});

describe("pickUserExecEnv", () => {
  it("keeps API keys and drops PATH / linker hijacks", () => {
    expect(
      pickUserExecEnv({
        AGNES_API_KEY: "sk-test-key-value",
        PATH: "/evil",
        LD_PRELOAD: "x",
        AGENTCORE_EXTERNAL_X: "/tmp",
        NPM_CONFIG_REGISTRY: "https://example.invalid/",
      }),
    ).toEqual({
      AGNES_API_KEY: "sk-test-key-value",
      NPM_CONFIG_REGISTRY: "https://example.invalid/",
    });
  });

  it("returns empty for non-objects", () => {
    expect(pickUserExecEnv(null)).toEqual({});
    expect(pickUserExecEnv("x")).toEqual({});
  });
});
