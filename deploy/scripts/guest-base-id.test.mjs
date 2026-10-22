/**
 * guest-base content id: GNU sha256sum listing format, 16-hex.
 * Run: node --test deploy/scripts/guest-base-id.test.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { GUEST_INPUTS, guestBaseId, readBuildPins } from "./guest-base-id.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "../..");
const serverRoot = join(repoRoot, "apps/server");

describe("guestBaseId", () => {
  it("hashes the frozen guest inputs in GNU sha256sum listing format", () => {
    const id = guestBaseId(serverRoot);
    assert.match(id, /^[0-9a-f]{16}$/);
    assert.deepEqual([...GUEST_INPUTS], [
      "Dockerfile.guest",
      "build-pins.env",
      "agentcore/tools/sandbox/cloud_python.txt",
    ]);
  });

  it("changes when any guest input changes", () => {
    const dir = mkdtempSync(join(tmpdir(), "guest-base-id-"));
    mkdirSync(join(dir, "agentcore/tools/sandbox"), { recursive: true });
    writeFileSync(join(dir, "Dockerfile.guest"), "FROM pin\n");
    writeFileSync(join(dir, "build-pins.env"), "PYTHON_IMAGE=x\n");
    writeFileSync(join(dir, "agentcore/tools/sandbox/cloud_python.txt"), "pdfplumber\n");
    const a = guestBaseId(dir);
    writeFileSync(join(dir, "Dockerfile.guest"), "FROM pin\n# bump\n");
    const b = guestBaseId(dir);
    assert.notEqual(a, b);
    assert.match(a, /^[0-9a-f]{16}$/);
    assert.match(b, /^[0-9a-f]{16}$/);
  });

  it("matches GNU sha256sum | sha256sum | cut -c1-16 when sha256sum exists", () => {
    const probe = spawnSync("sha256sum", ["--version"], { encoding: "utf8" });
    if (probe.error || probe.status !== 0) {
      return;
    }
    const listed = spawnSync(
      "sha256sum",
      [...GUEST_INPUTS],
      { cwd: serverRoot, encoding: "utf8" },
    );
    assert.equal(listed.status, 0, listed.stderr);
    const outer = spawnSync("sha256sum", { input: listed.stdout, encoding: "utf8" });
    assert.equal(outer.status, 0, outer.stderr);
    const gnu = outer.stdout.slice(0, 16);
    assert.equal(guestBaseId(serverRoot), gnu);
  });
});

describe("readBuildPins", () => {
  it("loads digest-pinned python and playwright host", () => {
    const pins = readBuildPins(serverRoot);
    assert.match(pins.PYTHON_IMAGE, /^python:3\.12-slim@sha256:[0-9a-f]{64}$/);
    assert.equal(pins.PLAYWRIGHT_VERSION, "1.61.0");
    assert.equal(pins.PLAYWRIGHT_DOWNLOAD_HOST, "https://npmmirror.com/mirrors/playwright");
  });
});
