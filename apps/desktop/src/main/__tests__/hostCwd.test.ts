/**
 * host(action=shell) cwd must land in an authorized root.
 * @vitest-environment node
 */
import os from "node:os";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  app: { getPath: () => tmpdir() },
}));

import { type StoredRoot, __test } from "../fs/roots";
import {
  HOST_SHELL_CWD_DENIED,
  isAbsInsideRoot,
  resolveHostShellCwd,
} from "../host/cwd";

const workspace: StoredRoot = {
  id: "perm-1",
  name: "ws",
  absPath: resolve("/tmp/agentcore-host-ws"),
};

const attach: StoredRoot = {
  id: "sess-1",
  name: "trade",
  absPath: resolve("/tmp/agentcore-host-attach"),
  sessionOnly: true,
  conversationId: "c1",
  mode: "attach_rw",
  alias: "trade",
};

afterEach(() => {
  __test.reset();
});

describe("isAbsInsideRoot", () => {
  it("accepts the root itself and a child", () => {
    const root = resolve("/tmp/ws");
    expect(isAbsInsideRoot(root, root)).toBe(true);
    expect(isAbsInsideRoot(resolve("/tmp/ws/src"), root)).toBe(true);
  });

  it("rejects a sibling", () => {
    expect(isAbsInsideRoot(resolve("/tmp/other"), resolve("/tmp/ws"))).toBe(
      false,
    );
  });
});

describe("resolveHostShellCwd", () => {
  it("falls back to homedir when no roots are registered", async () => {
    __test.reset();
    const got = await resolveHostShellCwd({});
    expect(got).toEqual({ ok: true, cwd: os.homedir() });
  });

  it("defaults to the permanent workspace root", async () => {
    __test.reset(new Map([[workspace.id, workspace]]));
    const got = await resolveHostShellCwd({ conversationId: "c1" });
    expect(got).toEqual({ ok: true, cwd: workspace.absPath });
  });

  it("accepts an attach_rw session root for this conversation", async () => {
    __test.reset(
      new Map([
        [workspace.id, workspace],
        [attach.id, attach],
      ]),
    );
    const got = await resolveHostShellCwd({
      conversationId: "c1",
      cwd: attach.absPath,
    });
    expect(got).toEqual({ ok: true, cwd: attach.absPath });
  });

  it("rejects a path outside every authorized root", async () => {
    __test.reset(new Map([[workspace.id, workspace]]));
    const got = await resolveHostShellCwd({
      conversationId: "c1",
      cwd: resolve("/etc"),
    });
    expect(got).toEqual({ ok: false, error: HOST_SHELL_CWD_DENIED });
  });

  it("resolves cwd from bound root_id", async () => {
    __test.reset(new Map([[workspace.id, workspace]]));
    const got = await resolveHostShellCwd({ rootId: "perm-1" });
    expect(got).toEqual({ ok: true, cwd: workspace.absPath });
  });

  it("does not enlarge the authorized set from an ungranted rootId", async () => {
    __test.reset(
      new Map([
        [workspace.id, workspace],
        [attach.id, attach],
      ]),
    );
    const got = await resolveHostShellCwd({
      rootId: attach.id,
      cwd: attach.absPath,
    });
    expect(got).toEqual({ ok: false, error: HOST_SHELL_CWD_DENIED });
  });
});
