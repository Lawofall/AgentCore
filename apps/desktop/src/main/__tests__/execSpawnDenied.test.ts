/**
 * Local execute: spawn-time EACCES/EPERM is declared at child.on("error"),
 * not guessed from a user script's PermissionError traceback.
 */
import type { ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import type { WorkspaceOpResult } from "@shared/ipc-contract";
import { describe, expect, it, vi } from "vitest";

const { spawnMock } = vi.hoisted(() => ({ spawnMock: vi.fn() }));

vi.mock("node:child_process", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:child_process")>();
  return { ...actual, spawn: spawnMock };
});

import { runSubprocess } from "../fs/workspace/exec";
import {
  EXEC_ENV_PROBE_FAIL_MARKER,
  EXEC_ENV_SPAWN_DENIED_CODE,
} from "../fs/workspace/execCodec";

interface ExecValue {
  success: boolean;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
}

function execValue(res: WorkspaceOpResult): ExecValue {
  if (!res.ok) throw new Error(`expected ok envelope, got ${res.error.kind}`);
  return res.value as ExecValue;
}

function errno(code: string, message: string): NodeJS.ErrnoException {
  const err = new Error(message) as NodeJS.ErrnoException;
  err.code = code;
  return err;
}

function fakeChild(err: NodeJS.ErrnoException, pid?: number): ChildProcess {
  const child = new EventEmitter() as ChildProcess;
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const stdin = new EventEmitter() as ChildProcess["stdin"];
  Object.assign(stdin as object, {
    write: () => true,
    end: () => {},
  });
  child.stdout = stdout as ChildProcess["stdout"];
  child.stderr = stderr as ChildProcess["stderr"];
  child.stdin = stdin;
  Object.assign(child as object, { pid });
  queueMicrotask(() => child.emit("error", err));
  return child;
}

describe("runSubprocess spawn-denied tag", () => {
  it("tags a refused start (EACCES, no pid) with the exec-env marker", async () => {
    spawnMock.mockImplementation(() =>
      fakeChild(errno("EACCES", "spawn python EACCES")),
    );
    const value = execValue(
      await runSubprocess(["python"], "main.py", ".", null, 5, Date.now()),
    );
    expect(value.success).toBe(false);
    expect(value.exit_code).toBe(-1);
    expect(value.stderr).toContain(EXEC_ENV_PROBE_FAIL_MARKER);
    expect(value.stderr).toContain(`[${EXEC_ENV_SPAWN_DENIED_CODE}]`);
    expect(value.stderr).toContain("spawn python EACCES");
    expect(value.stderr).not.toContain("Failed to start process:");
  });

  it("does not tag a missing binary (ENOENT)", async () => {
    spawnMock.mockImplementation(() =>
      fakeChild(errno("ENOENT", "spawn python ENOENT")),
    );
    const value = execValue(
      await runSubprocess(["python"], "main.py", ".", null, 5, Date.now()),
    );
    expect(value.stderr).toContain("Failed to start process:");
    expect(value.stderr).not.toContain(EXEC_ENV_PROBE_FAIL_MARKER);
  });

  it("does not tag EACCES after the process already has a pid", async () => {
    spawnMock.mockImplementation(() =>
      fakeChild(errno("EACCES", "spawn python EACCES"), 4242),
    );
    const value = execValue(
      await runSubprocess(["python"], "main.py", ".", null, 5, Date.now()),
    );
    expect(value.stderr).toContain("Failed to start process:");
    expect(value.stderr).not.toContain(EXEC_ENV_PROBE_FAIL_MARKER);
  });
});
