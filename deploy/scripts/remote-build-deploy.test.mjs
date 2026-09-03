/**
 * CLI 解析：默认预构建，--switch / --now 互斥。
 * Run: node --test deploy/scripts/remote-build-deploy.test.mjs
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { parseRemoteDeployArgs } from "./remote-build-deploy.mjs";

describe("parseRemoteDeployArgs", () => {
  it("defaults to prepare", () => {
    assert.deepEqual(parseRemoteDeployArgs(["695cec861"]), {
      mode: "prepare",
      sha: "695cec861",
    });
  });

  it("accepts explicit --prepare after sha", () => {
    assert.deepEqual(parseRemoteDeployArgs(["695cec861", "--prepare"]), {
      mode: "prepare",
      sha: "695cec861",
    });
  });

  it("parses --switch before or after sha", () => {
    assert.deepEqual(parseRemoteDeployArgs(["--switch", "695cec861"]), {
      mode: "switch",
      sha: "695cec861",
    });
    assert.deepEqual(parseRemoteDeployArgs(["695cec861", "--switch"]), {
      mode: "switch",
      sha: "695cec861",
    });
  });

  it("parses --now", () => {
    assert.deepEqual(parseRemoteDeployArgs(["--now", "abc1234"]), {
      mode: "now",
      sha: "abc1234",
    });
  });

  it("strips a lone -- (pnpm extra-args)", () => {
    assert.deepEqual(parseRemoteDeployArgs(["--", "--switch", "695cec861"]), {
      mode: "switch",
      sha: "695cec861",
    });
  });

  it("rejects conflicting modes", () => {
    assert.throws(
      () => parseRemoteDeployArgs(["--prepare", "--switch", "695cec861"]),
      /conflicting modes/,
    );
  });

  it("rejects missing sha and unknown flags", () => {
    assert.throws(() => parseRemoteDeployArgs([]), /usage:/);
    assert.throws(() => parseRemoteDeployArgs(["--switch"]), /usage:/);
    assert.throws(() => parseRemoteDeployArgs(["--force", "695cec861"]), /unknown flag/);
    assert.throws(() => parseRemoteDeployArgs(["not-a-sha"]), /invalid SHA/);
  });
});
