/**
 * Regression: concurrent local-store writes must not race on meta.json.
 *
 * The renderer fires cacheShellMeta from several unawaited effects (auth,
 * workspaces, conversations), so these IPC handlers genuinely interleave.
 * Before the write queue that produced two failures: a doubly-renamed shared
 * `meta.json.tmp` (ENOENT / EPERM on Windows), and a silent lost update where
 * the last read-modify-write clobbered the other patches.
 */
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const { TEST_ROOT } = vi.hoisted(() => {
  const tmp =
    globalThis.process.env.TMPDIR ??
    globalThis.process.env.TEMP ??
    globalThis.process.env.TMP ??
    "/tmp";
  return {
    TEST_ROOT: `${tmp}/local-store-concurrency-${globalThis.process.pid}`,
  };
});

vi.mock("electron", () => ({
  app: { getPath: () => TEST_ROOT },
  ipcMain: { handle: vi.fn() },
}));

import { rm } from "node:fs/promises";
import {
  LOCAL_STORE_CHANNELS,
  type LocalStoreFolderMeta,
  type LocalStorePutShellMeta,
  type LocalStoreShellMeta,
  type LocalStoreSnapshot,
  type LocalStoreUser,
  type LocalStoreWorkspaceMeta,
} from "@shared/local-store-contract";
import { ipcMain } from "electron";
import { registerLocalStoreIpc } from "../local-store";

type Handler = (event: unknown, arg?: unknown) => Promise<unknown>;

function handlerFor(channel: string): Handler {
  const calls = (ipcMain.handle as unknown as { mock: { calls: unknown[][] } })
    .mock.calls;
  const found = calls.find((c) => c[0] === channel);
  if (!found) throw new Error(`no handler registered for ${channel}`);
  return found[1] as Handler;
}

const user: LocalStoreUser = {
  id: "u1",
  username: "dev",
  displayName: "Dev",
  email: null,
  emailVerifiedAt: null,
  role: "user",
  avatarUrl: null,
};

const folder: LocalStoreFolderMeta = {
  id: "f1",
  name: "工作",
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
};

const workspace: LocalStoreWorkspaceMeta = {
  wsId: "w1",
  name: "ws",
  location: "cloud",
  rootId: null,
  subpath: "",
  hasFiles: false,
};

registerLocalStoreIpc();

describe("local-store concurrent writes", () => {
  beforeEach(async () => {
    await rm(TEST_ROOT, { recursive: true, force: true });
  });

  afterAll(async () => {
    await rm(TEST_ROOT, { recursive: true, force: true });
  });

  it("merges interleaved putShellMeta patches instead of clobbering", async () => {
    const put = handlerFor(LOCAL_STORE_CHANNELS.putShellMeta);
    const patches: LocalStorePutShellMeta[] = [
      { user },
      { folders: [folder] },
      { workspaces: [workspace] },
    ];

    // Fired together, exactly like the renderer's unawaited effects.
    const results = (await Promise.all(
      patches.map((p) => put(null, p)),
    )) as LocalStoreShellMeta[];
    expect(results).toHaveLength(3);

    const getSnapshot = handlerFor(LOCAL_STORE_CHANNELS.getSnapshot);
    const snapshot = (await getSnapshot(null)) as LocalStoreSnapshot | null;

    // Pre-fix: last writer wins — two of these three are gone.
    expect(snapshot?.user?.id).toBe("u1");
    expect(snapshot?.folders.map((f) => f.id)).toEqual(["f1"]);
    expect(snapshot?.workspaces.map((w) => w.wsId)).toEqual(["w1"]);
  });

  it("survives a burst of writes without a tmp-rename race", async () => {
    const put = handlerFor(LOCAL_STORE_CHANNELS.putShellMeta);
    await put(null, { user }); // so getSnapshot has something to return
    // Pre-fix on Windows this rejects with ENOENT/EPERM on meta.json.tmp.
    await expect(
      Promise.all(
        Array.from({ length: 30 }, (_, i) =>
          put(null, { folders: [{ ...folder, id: `f${i}` }] }),
        ),
      ),
    ).resolves.toHaveLength(30);

    const getSnapshot = handlerFor(LOCAL_STORE_CHANNELS.getSnapshot);
    const snapshot = (await getSnapshot(null)) as LocalStoreSnapshot | null;
    expect(snapshot?.folders).toHaveLength(1);
  });
});
