/**
 * CLI 解析：默认预构建，--switch / --now 互斥。
 * Run: node --test deploy/scripts/remote-build-deploy.test.mjs
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseRemoteDeployArgs, remoteScript } from "./remote-build-deploy.mjs";

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

describe("prepare script", () => {
  it("builds guest-base by content id then api with registry cache", () => {
    const script = remoteScript("prepare", "abc1234");
    assert.match(script, /docker buildx create --name agentcore-cache --driver docker-container/);
    assert.match(script, /--config "\$CFG"/);
    assert.match(script, /DOCKERHUB_MIRROR/);
    assert.match(script, /cut -d= -f2- \|\| true/);
    assert.match(script, /\[registry\."docker\.io"\]/);
    assert.match(script, /sha256sum Dockerfile\.guest build-pins\.env/);
    assert.match(script, /guest-base:\$\{GUEST_ID\}/);
    assert.match(script, /type=registry,ref=\$CACHE_GUEST/);
    assert.match(script, /type=registry,ref=\$CACHE_API/);
    assert.match(script, /--build-context "guest_image=docker-image:\/\/\$GUEST_IMAGE"/);
    assert.match(script, /--provenance=false --sbom=false/);
    assert.match(script, /PLAYWRIGHT_DOWNLOAD_HOST/);
    assert.match(script, /docker pull "\$IMAGE"/);
    assert.doesNotMatch(script, /docker buildx build --progress=plain --load/);
  });

  it("does not rebuild on switch", () => {
    const script = remoteScript("switch", "abc1234");
    assert.doesNotMatch(script, /Dockerfile\.guest/);
    assert.match(script, /finish-server\.sh/);
  });
});

describe("cutover script pins", () => {
  const dir = dirname(fileURLToPath(import.meta.url));

  it("finish-server records last-deployed-sha after readyz and ignores oneshot orphans", () => {
    const text = readFileSync(join(dir, "finish-server.sh"), "utf8");
    assert.match(text, /COMPOSE_IGNORE_ORPHANS=1/);
    assert.match(text, /\.last-deployed-sha/);
    assert.match(text, /printf '%s\\n' "\$TAG" >"\$SHA_FILE"/);
    assert.match(text, /KEEP_GUEST_IMAGES="\$\{KEEP_GUEST_IMAGES:-3\}"/);
    assert.match(text, /guest-base/);
    assert.match(text, /agentcore-cache/);
  });

  it("GHA backup bake pushes guest-base then api", () => {
    const text = readFileSync(
      join(dir, "../../.github/workflows/build-backend.yml"),
      "utf8",
    );
    assert.match(text, /docker\/bake-action@v6/);
    assert.match(text, /targets: guest/);
    assert.match(text, /targets: api/);
    assert.match(text, /GUEST_BASE=docker-image:\/\//);
    assert.match(text, /provenance: false/);
  });

  it("backup.sh defaults BACKUP_KEEP to 7", () => {
    const text = readFileSync(join(dir, "backup.sh"), "utf8");
    assert.match(text, /BACKUP_KEEP="\$\{BACKUP_KEEP:-7\}"/);
    assert.doesNotMatch(text, /BACKUP_KEEP="\$\{BACKUP_KEEP:-14\}"/);
  });
});
