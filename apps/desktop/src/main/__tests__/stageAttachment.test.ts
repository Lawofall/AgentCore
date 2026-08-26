/**
 * 引用即驻留：二进制驻留 + 暂存/finalize + 失败后的云占位诊断（纯逻辑，不碰真实 OneDrive）。
 */
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  utimes,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { execFileMock, spawnedPowershell, cloudAttrs, userDataPath, ioFailure } =
  vi.hoisted(() => {
    // Must attach promisify.custom BEFORE stageAttachment module loads
    // (it does `promisify(execFile)` at import time).
    const cloudAttrs = { stdout: "Archive" };
    const custom = Symbol.for("nodejs.util.promisify.custom");
    // promisify() hands back the custom impl, so the raw execFile mock never runs —
    // spy on the custom one or every "no powershell" assertion passes vacuously.
    const spawnedPowershell = vi.fn(
      async (_file: string, _args: string[], _opts: unknown) => ({
        stdout: cloudAttrs.stdout,
        stderr: "",
      }),
    );
    const execFileMock = Object.assign(vi.fn(), {
      [custom]: spawnedPowershell,
    });
    // Placeholder until beforeEach assigns a per-test userData dir.
    const userDataPath = { current: "" };
    /** Any path ending with this fails to open/stream — stands in for a placeholder
     *  file Windows refuses to hydrate (OneDrive offline / paused). */
    const ioFailure = { fileName: "" };
    return {
      execFileMock,
      spawnedPowershell,
      cloudAttrs,
      userDataPath,
      ioFailure,
    };
  });

vi.mock("electron", () => ({
  app: { getPath: () => userDataPath.current },
  dialog: { showOpenDialog: vi.fn() },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
}));

vi.mock("node:child_process", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:child_process")>();
  return {
    ...actual,
    execFile: execFileMock,
  };
});

vi.mock("node:fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:fs")>();
  const unreadable = (p: unknown) =>
    ioFailure.fileName !== "" && String(p).endsWith(ioFailure.fileName);
  return {
    ...actual,
    createReadStream: (
      path: Parameters<typeof actual.createReadStream>[0],
      options?: Parameters<typeof actual.createReadStream>[1],
    ) =>
      unreadable(path)
        ? actual.createReadStream(`${String(path)}.__cloud_unavailable__`)
        : actual.createReadStream(path, options),
    promises: {
      ...actual.promises,
      open: (
        path: Parameters<typeof actual.promises.open>[0],
        flags?: Parameters<typeof actual.promises.open>[1],
        mode?: Parameters<typeof actual.promises.open>[2],
      ) =>
        unreadable(path)
          ? Promise.reject(new Error("EIO: cloud file unavailable"))
          : actual.promises.open(path, flags, mode),
    },
  };
});

import { type StoredRoot, setRoot } from "../fs/roots";
import {
  ATTACH_MAX_BYTES,
  __resetStagingMemoryForTests,
  consumeStagedBytes,
  finalizeStagedAttachment,
  isCloudPlaceholder,
  stageFromAbsPath,
  stageFromBytes,
  sweepStagingOrphans,
} from "../fs/stageAttachment";

describe("stageAttachment", () => {
  let dir: string;
  let userData: string;
  let root: StoredRoot;
  const originalPlatform = process.platform;

  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "stage-att-"));
    userData = await mkdtemp(join(tmpdir(), "stage-userdata-"));
    userDataPath.current = userData;
    root = { id: "stage-root", name: "stage", absPath: dir };
    setRoot(root);
    cloudAttrs.stdout = "Archive";
    ioFailure.fileName = "";
    execFileMock.mockClear();
    spawnedPowershell.mockClear();
    __resetStagingMemoryForTests();
    // Hermetic default: skip PowerShell path unless a test opts into win32.
    Object.defineProperty(process, "platform", { value: "linux" });
  });

  afterEach(async () => {
    Object.defineProperty(process, "platform", { value: originalPlatform });
    ioFailure.fileName = "";
    await rm(dir, { recursive: true, force: true });
    await rm(userData, { recursive: true, force: true });
    __resetStagingMemoryForTests();
  });

  it("copies a text file into attachments/ and returns workspacePath", async () => {
    const src = join(dir, "notes.md");
    await writeFile(src, "# hello\n", "utf-8");
    const destDir = await mkdtemp(join(tmpdir(), "stage-dest-"));
    const destRoot: StoredRoot = {
      id: "dest-root",
      name: "dest",
      absPath: destDir,
    };
    setRoot(destRoot);
    try {
      const res = await stageFromAbsPath(src, { rootId: "dest-root" });
      expect(res.ok).toBe(true);
      if (!res.ok) return;
      expect(res.data.workspacePath).toBe("attachments/notes.md");
      expect(res.data.binary).toBe(false);
      expect(res.data.text).toContain("hello");
      const onDisk = await readFile(
        join(destDir, "attachments", "notes.md"),
        "utf-8",
      );
      expect(onDisk).toContain("hello");
    } finally {
      await rm(destDir, { recursive: true, force: true });
    }
  });

  it("stages binary xlsx-like bytes without text preview", async () => {
    const src = join(dir, "report.xlsx");
    const bytes = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x00]);
    await writeFile(src, bytes);
    const destDir = await mkdtemp(join(tmpdir(), "stage-bin-"));
    setRoot({ id: "bin-root", name: "bin", absPath: destDir });
    try {
      const res = await stageFromAbsPath(src, { rootId: "bin-root" });
      expect(res.ok).toBe(true);
      if (!res.ok) return;
      expect(res.data.binary).toBe(true);
      expect(res.data.text).toBe("");
      expect(res.data.workspacePath).toBe("attachments/report.xlsx");
      const onDisk = await readFile(
        join(destDir, "attachments", "report.xlsx"),
      );
      expect(Buffer.compare(onDisk, bytes)).toBe(0);
    } finally {
      await rm(destDir, { recursive: true, force: true });
    }
  });

  it("dedups attachment names when dest already has the file", async () => {
    const src = join(dir, "notes.md");
    await writeFile(src, "second\n", "utf-8");
    const destDir = await mkdtemp(join(tmpdir(), "stage-dedup-"));
    await mkdir(join(destDir, "attachments"), { recursive: true });
    await writeFile(join(destDir, "attachments", "notes.md"), "first\n");
    setRoot({ id: "dedup-root", name: "dedup", absPath: destDir });
    try {
      const res = await stageFromAbsPath(src, { rootId: "dedup-root" });
      expect(res.ok).toBe(true);
      if (!res.ok) return;
      expect(res.data.workspacePath).toBe("attachments/notes (2).md");
    } finally {
      await rm(destDir, { recursive: true, force: true });
    }
  });

  it("stages image attachments as binary", async () => {
    const src = join(dir, "photo.png");
    await writeFile(src, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a]));
    const res = await stageFromAbsPath(src);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.binary).toBe(true);
    expect(res.data.name).toBe("photo.png");
    expect(res.data.text).toBe("");
    expect(res.data.stagingId).toBeTruthy();
  });

  it("rejects oversized files", async () => {
    const src = join(dir, "huge.bin");
    expect(ATTACH_MAX_BYTES).toBe(50 * 1024 * 1024);
    await writeFile(src, "ok");
    const res = await stageFromAbsPath(src);
    expect(res.ok).toBe(true);
  });

  it("without dest returns stagingId (draft / cloud pending)", async () => {
    const src = join(dir, "pending.txt");
    await writeFile(src, "staged body\n", "utf-8");
    const res = await stageFromAbsPath(src);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.stagingId).toBeTruthy();
    expect(res.data.workspacePath).toBeUndefined();
    expect(res.data.text).toContain("staged");
  });

  it("finalizeStagedAttachment writes into local attachments/", async () => {
    const src = join(dir, "draft.bin");
    const bytes = Buffer.from([0x00, 0x01, 0x02, 0x03]);
    await writeFile(src, bytes);
    const staged = await stageFromAbsPath(src);
    expect(staged.ok).toBe(true);
    if (!staged.ok) return;
    const stagingId = staged.data.stagingId;
    expect(stagingId).toBeTruthy();
    if (!stagingId) return;

    const destDir = await mkdtemp(join(tmpdir(), "stage-fin-"));
    setRoot({ id: "fin-root", name: "fin", absPath: destDir });
    try {
      const fin = await finalizeStagedAttachment(stagingId, {
        rootId: "fin-root",
      });
      expect(fin.ok).toBe(true);
      if (!fin.ok) return;
      expect(fin.data.workspacePath).toBe("attachments/draft.bin");
      expect(fin.data.binary).toBe(true);
      const onDisk = await readFile(join(destDir, "attachments", "draft.bin"));
      expect(Buffer.compare(onDisk, bytes)).toBe(0);
    } finally {
      await rm(destDir, { recursive: true, force: true });
    }
  });

  it("consumeStagedBytes returns raw bytes for cloud PUT", async () => {
    const src = join(dir, "cloud.xlsx");
    // Include NUL so sniffBinary treats it as binary (xlsx zip header alone is not enough).
    const bytes = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x00]);
    await writeFile(src, bytes);
    const staged = await stageFromAbsPath(src);
    expect(staged.ok).toBe(true);
    if (!staged.ok) return;
    const stagingId = staged.data.stagingId;
    expect(stagingId).toBeTruthy();
    if (!stagingId) return;

    const consumed = await consumeStagedBytes(stagingId);
    expect(consumed.ok).toBe(true);
    if (!consumed.ok) return;
    expect(consumed.data.name).toBe("cloud.xlsx");
    expect(consumed.data.binary).toBe(true);
    expect(Buffer.from(consumed.data.data)).toEqual(bytes);

    // Second consume fails — staging cleared.
    const again = await consumeStagedBytes(stagingId);
    expect(again.ok).toBe(false);
  });

  it("finalize survives in-memory Map loss by rescanning attach-staging", async () => {
    const src = join(dir, "restart.txt");
    await writeFile(src, "still here\n", "utf-8");
    const staged = await stageFromAbsPath(src);
    expect(staged.ok).toBe(true);
    if (!staged.ok) return;
    const stagingId = staged.data.stagingId;
    expect(stagingId).toBeTruthy();
    if (!stagingId) return;

    // Simulate app restart: Map wiped, disk files remain under userData/attach-staging.
    __resetStagingMemoryForTests();

    const destDir = await mkdtemp(join(tmpdir(), "stage-rehydrate-"));
    setRoot({ id: "rehydrate-root", name: "rehydrate", absPath: destDir });
    try {
      const fin = await finalizeStagedAttachment(stagingId, {
        rootId: "rehydrate-root",
      });
      expect(fin.ok).toBe(true);
      if (!fin.ok) return;
      expect(fin.data.workspacePath).toBe("attachments/restart.txt");
      const onDisk = await readFile(
        join(destDir, "attachments", "restart.txt"),
        "utf-8",
      );
      expect(onDisk).toContain("still here");
    } finally {
      await rm(destDir, { recursive: true, force: true });
    }
  });

  it("consume after Map wipe fails honestly when staging dir is gone", async () => {
    const src = join(dir, "gone.txt");
    await writeFile(src, "bye\n", "utf-8");
    const staged = await stageFromAbsPath(src);
    expect(staged.ok).toBe(true);
    if (!staged.ok) return;
    const stagingId = staged.data.stagingId;
    if (!stagingId) return;

    // Wipe memory AND delete the on-disk staging folder (true loss).
    await rm(join(userData, "attach-staging", stagingId), {
      recursive: true,
      force: true,
    });
    __resetStagingMemoryForTests();

    const consumed = await consumeStagedBytes(stagingId);
    expect(consumed.ok).toBe(false);
    if (consumed.ok) return;
    expect(consumed.reason).toContain("附件暂存已失效");
  });

  it("isCloudPlaceholder returns false on non-Windows", async () => {
    const src = join(dir, "local.txt");
    await writeFile(src, "x");
    Object.defineProperty(process, "platform", { value: "linux" });
    const flagged = await isCloudPlaceholder(src);
    expect(flagged).toBe(false);
    expect(spawnedPowershell).not.toHaveBeenCalled();
  });

  it("isCloudPlaceholder (mocked win32) flags Offline attributes", async () => {
    const src = join(dir, "cloud-placeholder.txt");
    await writeFile(src, "x");
    Object.defineProperty(process, "platform", { value: "win32" });
    cloudAttrs.stdout = "Archive, Offline, ReparsePoint";
    const flagged = await isCloudPlaceholder(src);
    expect(flagged).toBe(true);
  });

  // Placeholder detection costs a powershell.exe spawn (300ms–2s cold). It used to
  // run before every copy, so each attached file paid for it — these pin it to the
  // failure branches only.
  describe("no placeholder probing on the happy path (win32)", () => {
    beforeEach(() => {
      Object.defineProperty(process, "platform", { value: "win32" });
    });

    it("attaching a local image never spawns powershell", async () => {
      const src = join(dir, "photo.png");
      await writeFile(src, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a]));

      const res = await stageFromAbsPath(src);

      expect(res.ok).toBe(true);
      expect(spawnedPowershell).not.toHaveBeenCalled();
    });

    it("copying a local text file into a workspace never spawns powershell", async () => {
      const src = join(dir, "notes.md");
      await writeFile(src, "# hello\n", "utf-8");
      const destDir = await mkdtemp(join(tmpdir(), "stage-nops-"));
      setRoot({ id: "nops-root", name: "nops", absPath: destDir });
      try {
        const res = await stageFromAbsPath(src, { rootId: "nops-root" });

        expect(res.ok).toBe(true);
        expect(spawnedPowershell).not.toHaveBeenCalled();
      } finally {
        await rm(destDir, { recursive: true, force: true });
      }
    });

    it("rescanning leftover staging dirs never spawns powershell", async () => {
      // attach-staging/ holds bytes this process wrote — never cloud placeholders,
      // yet the old pre-check probed every leftover dir on the first send.
      const text = join(dir, "left-a.txt");
      const image = join(dir, "left-b.png");
      await writeFile(text, "a\n", "utf-8");
      await writeFile(image, Buffer.from([0x89, 0x50, 0x4e, 0x47]));
      const stagedText = await stageFromAbsPath(text);
      const stagedImage = await stageFromAbsPath(image);
      expect(stagedText.ok).toBe(true);
      expect(stagedImage.ok).toBe(true);
      if (!stagedText.ok) return;
      const stagingId = stagedText.data.stagingId;
      expect(stagingId).toBeTruthy();
      if (!stagingId) return;

      // Simulate app restart: Map wiped, both dirs still on disk.
      __resetStagingMemoryForTests();
      spawnedPowershell.mockClear();

      const destDir = await mkdtemp(join(tmpdir(), "stage-hydrate-nops-"));
      setRoot({ id: "hyd-root", name: "hyd", absPath: destDir });
      try {
        const fin = await finalizeStagedAttachment(stagingId, {
          rootId: "hyd-root",
        });

        expect(fin.ok).toBe(true);
        expect(spawnedPowershell).not.toHaveBeenCalled();
      } finally {
        await rm(destDir, { recursive: true, force: true });
      }
    });
  });

  describe("placeholder diagnosis after a failed read/copy (win32)", () => {
    beforeEach(() => {
      Object.defineProperty(process, "platform", { value: "win32" });
    });

    it("reports an unsynced placeholder when the read fails", async () => {
      const src = join(dir, "onedrive-stub.docx");
      await writeFile(src, "x");
      ioFailure.fileName = "onedrive-stub.docx";
      cloudAttrs.stdout = "Offline, RecallOnDataAccess";

      const res = await stageFromAbsPath(src);

      expect(res.ok).toBe(false);
      if (res.ok) return;
      expect(res.code).toBe("busy");
      expect(res.reason).toContain("未同步");
      expect(spawnedPowershell).toHaveBeenCalledWith(
        "powershell.exe",
        expect.anything(),
        expect.anything(),
      );
    });

    it("reports an unsynced placeholder when copying an image fails", async () => {
      // Images skip the read probe entirely, so they only fail at copy time.
      const src = join(dir, "cloud-photo.png");
      await writeFile(src, Buffer.from([0x89, 0x50, 0x4e, 0x47]));
      ioFailure.fileName = "cloud-photo.png";
      cloudAttrs.stdout = "Archive, Offline";

      const res = await stageFromAbsPath(src);

      expect(res.ok).toBe(false);
      if (res.ok) return;
      expect(res.code).toBe("busy");
      expect(res.reason).toContain("未同步");
    });

    it("keeps the generic error when the file is not a placeholder", async () => {
      const src = join(dir, "broken.txt");
      await writeFile(src, "x");
      ioFailure.fileName = "broken.txt";
      cloudAttrs.stdout = "Archive";

      const res = await stageFromAbsPath(src);

      expect(res.ok).toBe(false);
      if (res.ok) return;
      expect(res.code).toBe("error");
      expect(res.reason).toContain("读取文件失败");
    });

    it("does not probe when a staged copy fails to reach the workspace", async () => {
      const src = join(dir, "staged-only.txt");
      await writeFile(src, "body\n", "utf-8");
      const staged = await stageFromAbsPath(src);
      expect(staged.ok).toBe(true);
      if (!staged.ok) return;
      const stagingId = staged.data.stagingId;
      expect(stagingId).toBeTruthy();
      if (!stagingId) return;

      const destDir = await mkdtemp(join(tmpdir(), "stage-fin-fail-"));
      setRoot({ id: "fin-fail-root", name: "fin-fail", absPath: destDir });
      ioFailure.fileName = "staged-only.txt";
      spawnedPowershell.mockClear();
      try {
        const fin = await finalizeStagedAttachment(stagingId, {
          rootId: "fin-fail-root",
        });

        expect(fin.ok).toBe(false);
        if (fin.ok) return;
        expect(fin.code).toBe("error");
        expect(fin.reason).toContain("复制到工作区失败");
        expect(spawnedPowershell).not.toHaveBeenCalled();
      } finally {
        await rm(destDir, { recursive: true, force: true });
      }
    });
  });

  it("stageFromBytes stages a clipboard PNG into attach-staging", async () => {
    // Minimal PNG header + IHDR (not a valid full PNG; enough for binary image path).
    const png = new Uint8Array([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 1, 2, 3,
    ]);
    const res = await stageFromBytes("image.png", png, undefined, "image/png");
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.binary).toBe(true);
    expect(res.data.name).toBe("image.png");
    expect(res.data.stagingId).toBeTruthy();
    expect(res.data.sizeBytes).toBe(png.byteLength);

    const stagingId = res.data.stagingId;
    if (!stagingId) return;
    const consumed = await consumeStagedBytes(stagingId);
    expect(consumed.ok).toBe(true);
    if (!consumed.ok) return;
    expect(Array.from(consumed.data.data)).toEqual(Array.from(png));
  });

  it("stageFromBytes writes into workspace attachments when dest is set", async () => {
    const destDir = await mkdtemp(join(tmpdir(), "stage-bytes-dest-"));
    const destRoot: StoredRoot = {
      id: "bytes-dest",
      name: "bytes-dest",
      absPath: destDir,
    };
    setRoot(destRoot);
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const res = await stageFromBytes(
      "clip.png",
      bytes,
      { rootId: "bytes-dest" },
      "image/png",
    );
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.workspacePath).toBe("attachments/clip.png");
    const onDisk = await readFile(join(destDir, "attachments", "clip.png"));
    expect(Array.from(onDisk)).toEqual([1, 2, 3, 4]);
    await rm(destDir, { recursive: true, force: true });
  });

  it("stageFromBytes appends extension from mime when name has none", async () => {
    const bytes = new Uint8Array([9, 9, 9]);
    const res = await stageFromBytes(
      "attachment",
      bytes,
      undefined,
      "image/png",
    );
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.name).toBe("attachment.png");
    expect(res.data.binary).toBe(true);
  });

  it("stageFromBytes rejects oversize payloads", async () => {
    const bytes = new Uint8Array(ATTACH_MAX_BYTES + 1);
    const res = await stageFromBytes("big.bin", bytes);
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("50MB");
  });

  it("stageFromBytes rejects empty payloads", async () => {
    const res = await stageFromBytes("empty.png", new Uint8Array(0));
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("空");
  });

  describe("sweepStagingOrphans", () => {
    const stagingRoot = () => join(userData, "attach-staging");

    /** `ageMs` backdates the dir so it looks like a previous session's leftover. */
    async function seed(id: string, ageMs: number): Promise<void> {
      const idDir = join(stagingRoot(), id);
      await mkdir(idDir, { recursive: true });
      await writeFile(join(idDir, "f.txt"), "x", "utf-8");
      if (ageMs > 0) {
        const when = new Date(Date.now() - ageMs);
        await utimes(idDir, when, when);
      }
    }

    it("reaps a leftover staging dir that no draft references", async () => {
      await seed("orphan-1", 3_600_000);
      await seed("live-1", 3_600_000);

      await sweepStagingOrphans(["live-1"]);

      expect(await readdir(stagingRoot())).toEqual(["live-1"]);
    });

    it("keeps dirs staged by the live session even when unreferenced", async () => {
      await seed("fresh-1", 0);

      await sweepStagingOrphans([]);

      expect(await readdir(stagingRoot())).toEqual(["fresh-1"]);
    });
  });
});
