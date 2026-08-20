/**
 * Unit tests for local execute codec: UTF-16LE pipe decode + WSL bash reject.
 * Mirrors apps/server/tests/test_sandbox_subprocess.py.
 */
import { afterEach, describe, expect, it } from "vitest";
import {
  EXEC_ENV_PROBE_FAIL_MARKER,
  EXEC_ENV_SPAWN_DENIED_CODE,
  _setPathExistsForTests,
  decodePipeChunk,
  isSpawnDeniedError,
  isWslBashTrampoline,
  probeAvailableLanguages,
  resolveBashLauncher,
  spawnDeniedStderr,
} from "../fs/workspace/execCodec";

describe("decodePipeChunk", () => {
  it("re-decodes NUL-dense UTF-16LE instead of w\\0s\\0l\\0 mojibake", () => {
    const text = "wsl: 局域网";
    const buf = Buffer.from(text, "utf16le");
    const decoded = decodePipeChunk(buf);
    expect(decoded).not.toContain("\0");
    expect(decoded).toContain("wsl");
    expect(decoded).toContain("局域网");
  });

  it("leaves ordinary UTF-8 stdout alone", () => {
    expect(decodePipeChunk(Buffer.from("hello stdout\n", "utf-8"))).toBe(
      "hello stdout\n",
    );
  });
});

describe("isWslBashTrampoline", () => {
  it("flags System32 / SysWOW64 bash.exe", () => {
    expect(isWslBashTrampoline(String.raw`C:\Windows\System32\bash.exe`)).toBe(
      true,
    );
    expect(isWslBashTrampoline(String.raw`C:\Windows\SysWOW64\bash.exe`)).toBe(
      true,
    );
  });

  it("does not flag Git Bash", () => {
    expect(
      isWslBashTrampoline(String.raw`C:\Program Files\Git\bin\bash.exe`),
    ).toBe(false);
  });
});

describe("resolveBashLauncher (Windows)", () => {
  const originalPlatform = process.platform;
  const originalPath = process.env.PATH;
  const originalLocal = process.env.LOCALAPPDATA;

  afterEach(() => {
    Object.defineProperty(process, "platform", {
      value: originalPlatform,
      configurable: true,
    });
    process.env.PATH = originalPath;
    if (originalLocal === undefined) process.env.LOCALAPPDATA = undefined;
    else process.env.LOCALAPPDATA = originalLocal;
    _setPathExistsForTests(null);
  });

  it("returns null when PATH only has a WSL trampoline", () => {
    Object.defineProperty(process, "platform", {
      value: "win32",
      configurable: true,
    });
    process.env.LOCALAPPDATA = undefined;
    process.env.PATH = String.raw`C:\Windows\System32`;
    _setPathExistsForTests((p) => {
      const s = String(p).replace(/\//g, "\\").toLowerCase();
      return s.endsWith(String.raw`\system32\bash.exe`);
    });
    expect(resolveBashLauncher()).toBeNull();
  });

  it("prefers Git Bash over a WSL trampoline on PATH", () => {
    Object.defineProperty(process, "platform", {
      value: "win32",
      configurable: true,
    });
    process.env.LOCALAPPDATA = undefined;
    process.env.PATH = String.raw`C:\Windows\System32`;
    const git = String.raw`C:\Program Files\Git\bin\bash.exe`;
    _setPathExistsForTests((p) => {
      const s = String(p).replace(/\//g, "\\").toLowerCase();
      return (
        s === git.toLowerCase() || s.endsWith(String.raw`\system32\bash.exe`)
      );
    });
    expect(resolveBashLauncher()).toBe(git);
  });
});

describe("probeAvailableLanguages (Windows)", () => {
  const originalPlatform = process.platform;
  const originalPath = process.env.PATH;
  const originalLocal = process.env.LOCALAPPDATA;

  afterEach(() => {
    Object.defineProperty(process, "platform", {
      value: originalPlatform,
      configurable: true,
    });
    process.env.PATH = originalPath;
    if (originalLocal === undefined) process.env.LOCALAPPDATA = undefined;
    else process.env.LOCALAPPDATA = originalLocal;
    _setPathExistsForTests(null);
  });

  it("omits bash when only a WSL trampoline is present", () => {
    Object.defineProperty(process, "platform", {
      value: "win32",
      configurable: true,
    });
    process.env.LOCALAPPDATA = undefined;
    process.env.PATH = [
      String.raw`C:\Python`,
      String.raw`C:\nodejs`,
      String.raw`C:\Windows\System32`,
    ].join(";");
    _setPathExistsForTests((p) => {
      const s = String(p).replace(/\//g, "\\").toLowerCase();
      return (
        s.endsWith(String.raw`\python\python.exe`) ||
        s.endsWith(String.raw`\nodejs\node.exe`) ||
        s.endsWith(String.raw`\system32\bash.exe`)
      );
    });
    const langs = probeAvailableLanguages();
    expect(langs).toContain("python");
    expect(langs).toContain("javascript");
    expect(langs).not.toContain("bash");
  });

  it("includes bash when Git Bash exists", () => {
    Object.defineProperty(process, "platform", {
      value: "win32",
      configurable: true,
    });
    process.env.LOCALAPPDATA = undefined;
    process.env.PATH = String.raw`C:\Python;C:\nodejs`;
    const git = String.raw`C:\Program Files\Git\bin\bash.exe`;
    _setPathExistsForTests((p) => {
      const s = String(p).replace(/\//g, "\\").toLowerCase();
      return (
        s === git.toLowerCase() ||
        s.endsWith(String.raw`\python\python.exe`) ||
        s.endsWith(String.raw`\nodejs\node.exe`)
      );
    });
    expect(probeAvailableLanguages()).toEqual(["python", "javascript", "bash"]);
  });
});

describe("spawnDeniedStderr", () => {
  it("emits the server-equal marker and reason tag", () => {
    expect(spawnDeniedStderr("spawn python EACCES")).toBe(
      `${EXEC_ENV_PROBE_FAIL_MARKER} [${EXEC_ENV_SPAWN_DENIED_CODE}] spawn python EACCES`,
    );
  });

  it("keys on err.code, never on the message prose", () => {
    const eacces = Object.assign(new Error("spawn python EACCES"), {
      code: "EACCES",
    });
    const eperm = Object.assign(new Error("spawn python EPERM"), {
      code: "EPERM",
    });
    const enoent = Object.assign(new Error("spawn python ENOENT"), {
      code: "ENOENT",
    });
    expect(isSpawnDeniedError(eacces)).toBe(true);
    expect(isSpawnDeniedError(eperm)).toBe(true);
    expect(isSpawnDeniedError(enoent)).toBe(false);
    expect(isSpawnDeniedError(new Error("Permission denied"))).toBe(false);
    expect(isSpawnDeniedError(new Error("spawn python EACCES"))).toBe(false);
  });
});
