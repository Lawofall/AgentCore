/**
 * Direct-path latest.json gate: skip feed while GitHub release is draft.
 * Run: node --test deploy/scripts/sync-release-cdn.test.mjs
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { RELEASES_REPO } from "../../apps/website/functions/_lib/downloadsCdn.mjs";
import {
  githubTagForRail,
  inspectGithubReleaseForFeed,
  runDirectRailSync,
} from "./sync-release-cdn.mjs";

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function tagUrl(tag) {
  return `https://api.github.com/repos/${RELEASES_REPO}/releases/tags/${encodeURIComponent(tag)}`;
}

function trackDirectIo() {
  /** @type {object[]} */
  const uploads = [];
  /** @type {object[]} */
  const feeds = [];
  /** @type {object[]} */
  const prunes = [];
  /** @type {string[]} */
  const warnings = [];
  return {
    uploads,
    feeds,
    prunes,
    warnings,
    deps: {
      uploadDesktop: (version, desktopDir, channel) => {
        uploads.push({ rail: "desktop", version, desktopDir, channel });
        return {
          present: [`AgentCore-${version}-win-x64.exe`],
          hasWin: true,
          hasMac: false,
          winName: `AgentCore-${version}-win-x64.exe`,
          macName: "",
        };
      },
      writeDesktopFeed: async (version, flags, channel) => {
        feeds.push({ rail: "desktop", version, flags, channel });
      },
      uploadAndroid: (version, androidPath) => {
        uploads.push({ rail: "android", version, androidPath });
        return { name: `AgentCore-${version}-android.apk` };
      },
      writeAndroidFeed: async (version, name) => {
        feeds.push({ rail: "android", version, name });
      },
      pruneDesktop: (version, channel) => {
        prunes.push({ rail: "desktop", version, channel });
      },
      pruneAndroid: (version) => {
        prunes.push({ rail: "android", version });
      },
      warn: (msg) => warnings.push(String(msg)),
    },
  };
}

describe("githubTagForRail", () => {
  it("uses v* for desktop and android-v* for android", () => {
    assert.equal(githubTagForRail("desktop", "0.9.4"), "v0.9.4");
    assert.equal(githubTagForRail("android", "0.4.7"), "android-v0.4.7");
  });
});

describe("inspectGithubReleaseForFeed", () => {
  it("treats public-API 404 as unpublished (typical draft)", async () => {
    /** @type {string[]} */
    const urls = [];
    const result = await inspectGithubReleaseForFeed("android", "0.4.7", {
      fetchImpl: async (url) => {
        urls.push(String(url));
        return jsonResponse(404, { message: "Not Found" });
      },
    });
    assert.deepEqual(urls, [tagUrl("android-v0.4.7")]);
    assert.equal(result.published, false);
    assert.equal(result.reason, "not-found");
    assert.equal(result.tag, "android-v0.4.7");
  });

  it("treats draft:true as unpublished", async () => {
    const result = await inspectGithubReleaseForFeed("desktop", "0.9.4", {
      fetchImpl: async () =>
        jsonResponse(200, { draft: true, tag_name: "v0.9.4" }),
    });
    assert.equal(result.published, false);
    assert.equal(result.reason, "draft");
  });

  it("treats non-draft as published", async () => {
    const result = await inspectGithubReleaseForFeed("android", "0.4.7", {
      fetchImpl: async () =>
        jsonResponse(200, { draft: false, tag_name: "android-v0.4.7" }),
    });
    assert.equal(result.published, true);
    assert.equal(result.reason, "published");
  });

  it("does not throw when GitHub is unreachable", async () => {
    const result = await inspectGithubReleaseForFeed("desktop", "0.9.4", {
      fetchImpl: async () => {
        throw new Error("network down");
      },
    });
    assert.equal(result.published, false);
    assert.equal(result.reason, "error");
  });
});

describe("runDirectRailSync android", () => {
  it("draft: uploads APK but skips latest.json", async () => {
    const io = trackDirectIo();
    const result = await runDirectRailSync({
      rail: "android",
      version: "0.4.7",
      androidPath: "C:\\fake\\AgentCore-0.4.7-android.apk",
      fetchImpl: async () => jsonResponse(404, { message: "Not Found" }),
      ...io.deps,
    });
    assert.equal(result.writeFeed, false);
    assert.equal(io.uploads.length, 1);
    assert.equal(io.uploads[0].rail, "android");
    assert.equal(io.uploads[0].version, "0.4.7");
    assert.deepEqual(io.feeds, []);
    assert.equal(io.prunes.length, 1);
    assert.match(io.warnings[0], /android-v0\.4\.7/);
    assert.match(io.warnings[0], /skipped latest\.json/);
    assert.match(io.warnings[0], /sync:release-cdn --from-github/);
  });

  it("published: uploads APK and writes latest.json", async () => {
    const io = trackDirectIo();
    const result = await runDirectRailSync({
      rail: "android",
      version: "0.4.7",
      androidPath: "C:\\fake\\AgentCore-0.4.7-android.apk",
      fetchImpl: async () =>
        jsonResponse(200, { draft: false, tag_name: "android-v0.4.7" }),
      ...io.deps,
    });
    assert.equal(result.writeFeed, true);
    assert.equal(io.uploads.length, 1);
    assert.deepEqual(io.feeds, [
      {
        rail: "android",
        version: "0.4.7",
        name: "AgentCore-0.4.7-android.apk",
      },
    ]);
    assert.deepEqual(io.warnings, []);
  });
});

describe("runDirectRailSync desktop", () => {
  it("draft: uploads artifacts but skips latest.json", async () => {
    const io = trackDirectIo();
    const result = await runDirectRailSync({
      rail: "desktop",
      version: "0.9.4",
      channel: "stable",
      desktopDir: "C:\\fake\\release",
      fetchImpl: async () =>
        jsonResponse(200, { draft: true, tag_name: "v0.9.4" }),
      ...io.deps,
    });
    assert.equal(result.writeFeed, false);
    assert.equal(io.uploads.length, 1);
    assert.equal(io.uploads[0].rail, "desktop");
    assert.equal(io.uploads[0].version, "0.9.4");
    assert.deepEqual(io.feeds, []);
    assert.equal(io.prunes.length, 1);
    assert.match(io.warnings[0], /v0\.9\.4/);
    assert.match(io.warnings[0], /skipped latest\.json/);
    assert.match(io.warnings[0], /sync:release-cdn --from-github/);
  });

  it("published: uploads artifacts and writes latest.json", async () => {
    const io = trackDirectIo();
    const result = await runDirectRailSync({
      rail: "desktop",
      version: "0.9.4",
      channel: "stable",
      desktopDir: "C:\\fake\\release",
      fetchImpl: async () =>
        jsonResponse(200, { draft: false, tag_name: "v0.9.4" }),
      ...io.deps,
    });
    assert.equal(result.writeFeed, true);
    assert.equal(io.uploads.length, 1);
    assert.equal(io.feeds.length, 1);
    assert.equal(io.feeds[0].rail, "desktop");
    assert.equal(io.feeds[0].version, "0.9.4");
    assert.deepEqual(io.warnings, []);
  });
});
