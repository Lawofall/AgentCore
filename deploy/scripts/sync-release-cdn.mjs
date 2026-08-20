#!/usr/bin/env node
/**
 * Sync desktop / Android release assets to the brand download host (self-hosted nginx).
 *
 *   pnpm sync:release-cdn --desktop <dir> --version <ver> [--channel stable|beta]
 *   pnpm sync:release-cdn --android <apkPath> --version <ver>
 *   pnpm sync:release-cdn --from-github [--channel stable|beta]
 *   pnpm sync:release-cdn --from-github --desktop-only
 *   pnpm sync:release-cdn --from-github --android-only
 *   pnpm sync:release-cdn --install-nginx            # one-time nginx site on :8092
 *   pnpm sync:release-cdn --prune-only [--channel stable|beta] [--prune-dry-run]
 *   pnpm sync:release-cdn --prune-only --android-only [--prune-dry-run]
 *
 * After a successful sync, each dest dir is pruned to the current feed version
 * (see prune-release-cdn.mjs). `--prune-dry-run` lists deletes without rm;
 * `--skip-prune` leaves old artifacts in place.
 *
 * Desktop channels (§7.6c):
 *   stable (default) → write desktop/stable/* and mirror same content to flat desktop/
 *     (旧客户端曾把 desktop/latest.yml 当 feed；镜像避免断更)
 *   beta → write desktop/beta/* only（绝不污染 flat 或 stable）
 *
 * Env (deploy/.env.deploy.local):
 *   DEPLOY_SSH_*                 (same as deploy:web / admin)
 *   AGENTCORE_DOWNLOADS_BASE     (optional; default https://downloads.fashitianxia.xyz)
 *   AGENTCORE_DOWNLOADS_HOST     (optional; nginx server_name / tunnel hostname)
 *   AGENTCORE_DOWNLOADS_ROOT     (optional; remote dir, default /opt/agentcore/downloads)
 *
 * Prerequisites: downloads-remote-install + Cloudflare Tunnel Public Hostname — §7.6b.
 */
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve as resolvePath } from "node:path";
import { pathToFileURL } from "node:url";
import {
  REPO_ROOT,
  loadDeployEnv,
  requireEnv,
  scp,
  sshCapture,
  sshScript,
} from "./load-deploy-env.mjs";
import {
  DESKTOP_CHANNEL_DEFAULT,
  DOWNLOADS_ANDROID_PREFIX,
  DOWNLOADS_DESKTOP_PREFIX,
  RELEASES_REPO,
  androidApkFilename,
  artifactUrlsForVersion,
  buildAndroidLatestJson,
  buildDesktopLatestJson,
  cdnUrl,
  desktopChannelPrefix,
  desktopLatestJsonUrl,
  desktopSyncDestPrefixes,
  macDmgFilename,
  normalizeDesktopChannel,
  winInstallerFilename,
} from "../../apps/website/functions/_lib/downloadsCdn.mjs";
import {
  desktopArtifactNames,
  isAndroidArtifact,
  isDesktopArtifact,
  isSafeBasename,
  keepHintsFromManifests,
  planPrune,
} from "./prune-release-cdn.mjs";

function parseArgs(argv) {
  /** @type {Record<string, string | boolean>} */
  const out = {
    desktopDir: "",
    androidPath: "",
    version: "",
    channel: DESKTOP_CHANNEL_DEFAULT,
    fromGithub: false,
    desktopOnly: false,
    androidOnly: false,
    installNginx: false,
    pruneDryRun: false,
    skipPrune: false,
    pruneOnly: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--desktop" && argv[i + 1]) out.desktopDir = argv[++i];
    else if (a === "--android" && argv[i + 1]) out.androidPath = argv[++i];
    else if (a === "--version" && argv[i + 1]) out.version = argv[++i];
    else if (a === "--channel" && argv[i + 1]) {
      out.channel = normalizeDesktopChannel(argv[++i]);
    } else if (a === "--from-github") out.fromGithub = true;
    else if (a === "--desktop-only") out.desktopOnly = true;
    else if (a === "--android-only") out.androidOnly = true;
    else if (a === "--install-nginx") out.installNginx = true;
    else if (a === "--prune-dry-run") out.pruneDryRun = true;
    else if (a === "--skip-prune") out.skipPrune = true;
    else if (a === "--prune-only") out.pruneOnly = true;
    else if (a === "--help" || a === "-h") out.help = true;
  }
  return out;
}

function downloadsRoot() {
  return (
    process.env.AGENTCORE_DOWNLOADS_ROOT?.trim() || "/opt/agentcore/downloads"
  ).replace(/\/+$/, "");
}

function downloadsHost() {
  return (
    process.env.AGENTCORE_DOWNLOADS_HOST?.trim() || "downloads.fashitianxia.xyz"
  );
}

/** Upload one local file to remote absolute path (creates parent dirs). */
function putRemoteFile(localPath, remoteAbsPath) {
  const remoteDir = dirname(remoteAbsPath).replace(/\\/g, "/");
  const tmpName = `ac-dl-${Date.now()}-${basename(localPath)}`;
  const tmpRemote = `/tmp/${tmpName}`;
  console.log(`→ scp ${basename(localPath)} → ${remoteAbsPath}`);
  scp(localPath, tmpRemote);
  sshScript(`set -euo pipefail
mkdir -p "${remoteDir}"
mv -f "${tmpRemote}" "${remoteAbsPath}"
`);
}

/** Upload every file under localDir into remoteAbsDir (flat). */
function putRemoteDirFiles(localDir, remoteAbsDir, names) {
  const list = names.filter((n) => existsSync(join(localDir, n)));
  if (list.length === 0) return [];
  // Per-file scp: Windows `tar -czf` often produces archives Linux GNU tar rejects
  // (trailing garbage / xattr), which broke release:win CDN sync.
  for (const n of list) {
    putRemoteFile(join(localDir, n), `${remoteAbsDir}/${n}`);
  }
  return list;
}

/** @type {{ dryRun: boolean, skip: boolean }} */
let pruneMode = { dryRun: false, skip: false };

const PRUNE_DEST_RELS = Object.freeze([
  "desktop",
  "desktop/stable",
  "desktop/beta",
  "android",
]);

function allowedPruneAbsDir(remoteAbsDir) {
  const root = downloadsRoot().replace(/\/$/, "").replace(/\\/g, "/");
  const normalized = String(remoteAbsDir).replace(/\\/g, "/").replace(/\/$/, "");
  return PRUNE_DEST_RELS.some((rel) => normalized === `${root}/${rel}`);
}

function bashQuote(value) {
  return JSON.stringify(String(value));
}

/** Depth-1 regular files only (never descend into stable/ / beta/). */
function listRemoteFiles(remoteAbsDir) {
  const { status, stdout, stderr } = sshCapture(
    `set -euo pipefail
if [[ ! -d ${bashQuote(remoteAbsDir)} ]]; then
  echo "__AC_PRUNE_MISSING__"
  exit 0
fi
find ${bashQuote(remoteAbsDir)} -maxdepth 1 -type f -exec basename {} \\;
`,
    { allowFail: true },
  );
  if (status !== 0) {
    throw new Error(
      `cannot list ${remoteAbsDir}: ${(stderr || stdout || `exit ${status}`).trim()}`,
    );
  }
  const text = stdout.replace(/\r\n/g, "\n");
  if (text.trim() === "__AC_PRUNE_MISSING__") return null;
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
    .filter((n) => n !== "__AC_PRUNE_MISSING__");
}

/**
 * ``""`` when the file is absent, ``null`` when the read itself failed.
 *
 * The two must not collapse: the keep set is built from these feeds, so a
 * transient SSH failure read as "absent" silently shrinks it. On a win-only
 * bump that deletes the mac artifacts while ``latest-mac.yml`` survives and
 * keeps naming them — mac clients 404 until the next mac release.
 */
function readRemoteOptionalFile(remoteAbsPath) {
  const { status, stdout } = sshCapture(
    `set -euo pipefail
if [[ -f ${bashQuote(remoteAbsPath)} ]]; then
  cat ${bashQuote(remoteAbsPath)}
fi
`,
    { allowFail: true },
  );
  if (status !== 0) return null;
  return stdout;
}

function deleteRemoteFiles(remoteAbsDir, names) {
  for (const n of names) {
    if (!isSafeBasename(n)) {
      throw new Error(`refusing to delete unsafe name ${JSON.stringify(n)}`);
    }
  }
  if (names.length === 0) return;
  const payload = names.join("\n");
  sshScript(`set -euo pipefail
cd ${bashQuote(remoteAbsDir)}
while IFS= read -r n; do
  [[ -z "$n" ]] && continue
  [[ "$n" == */* || "$n" == "." || "$n" == ".." ]] && continue
  if [[ -f "$n" && ! -L "$n" ]]; then
    rm -f -- "$n"
  fi
done <<'AC_PRUNE_EOF'
${payload}
AC_PRUNE_EOF
`);
}

/**
 * @param {string} remoteAbsDir
 * @param {{ kind: "desktop" | "android", currentVersion: string }} opts
 */
function pruneRemoteDest(remoteAbsDir, { kind, currentVersion }) {
  if (pruneMode.skip) return;
  if (!allowedPruneAbsDir(remoteAbsDir)) {
    throw new Error(`refusing prune outside downloads dest: ${remoteAbsDir}`);
  }
  const listed = listRemoteFiles(remoteAbsDir);
  if (listed === null) {
    console.warn(`⚠ prune skip (missing dir): ${remoteAbsDir}`);
    return;
  }
  const feeds = {
    latestJson: readRemoteOptionalFile(`${remoteAbsDir}/latest.json`),
    latestYml: readRemoteOptionalFile(`${remoteAbsDir}/latest.yml`),
    latestMacYml: readRemoteOptionalFile(`${remoteAbsDir}/latest-mac.yml`),
  };
  const unreadable = Object.keys(feeds).filter((k) => feeds[k] === null);
  if (unreadable.length > 0) {
    console.warn(
      `⚠ prune skip (feed unreadable: ${unreadable.join(", ")}): ${remoteAbsDir} — ` +
        `keep all ${listed.length} files. Same posture as a failed listing: without ` +
        `every feed we cannot prove a version has no consumer.`,
    );
    return;
  }
  const hints = keepHintsFromManifests(feeds);
  const version = String(currentVersion || "").trim() || hints.extraVersions[0] || "";
  const plan = planPrune({
    listedNames: listed,
    kind,
    currentVersion: version,
    extraVersions: hints.extraVersions,
    extraFilenames: hints.extraFilenames,
  });
  if (plan.skipped) {
    console.warn(
      `⚠ prune skip (${plan.skipped}): ${remoteAbsDir} — keep all ${listed.length} files`,
    );
    return;
  }
  const deletable = plan.delete.filter((n) =>
    kind === "android" ? isAndroidArtifact(n) : isDesktopArtifact(n),
  );
  if (deletable.length !== plan.delete.length) {
    console.warn(
      `⚠ prune dropped ${plan.delete.length - deletable.length} names that failed the artifact re-check`,
    );
  }
  const keepVers = plan.keepVersions.join(", ");
  if (deletable.length === 0) {
    console.log(
      `✓ prune ${remoteAbsDir}: nothing to remove (keep versions: ${keepVers}; ${plan.keep.length} files)`,
    );
    return;
  }
  if (pruneMode.dryRun) {
    console.log(
      `⚠ prune dry-run ${remoteAbsDir}: would delete ${deletable.length} files (keep versions: ${keepVers})`,
    );
    for (const n of deletable) console.log(`    ${n}`);
    return;
  }
  deleteRemoteFiles(remoteAbsDir, deletable);
  console.log(
    `✓ prune ${remoteAbsDir}: deleted ${deletable.length} old artifacts (keep versions: ${keepVers})`,
  );
}

/**
 * @param {string} version
 * @param {import("../../apps/website/functions/_lib/downloadsCdn.mjs").DesktopChannel} channel
 */
function pruneDesktopDests(version, channel) {
  const root = downloadsRoot();
  for (const rel of desktopSyncDestPrefixes(channel)) {
    pruneRemoteDest(`${root}/${rel}`, { kind: "desktop", currentVersion: version });
  }
}

/** @param {string} version */
function pruneAndroidDest(version) {
  const root = downloadsRoot();
  pruneRemoteDest(`${root}/${DOWNLOADS_ANDROID_PREFIX}`, {
    kind: "android",
    currentVersion: version,
  });
}

const GH_FETCH_HEADERS = Object.freeze({
  "User-Agent": "agentcore-sync-release-cdn",
  Accept: "application/json",
});

async function fetchJson(url) {
  const res = await fetch(url, {
    headers: {
      "User-Agent": "agentcore-sync-release-cdn",
      Accept: "application/json",
    },
  });
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  return res.json();
}

/** @typedef {"desktop" | "android"} ReleaseRail */

/**
 * GitHub tag for a version on each rail. Desktop = ``v<ver>``; Android =
 * ``android-v<ver>`` (same split as release:win / release:android).
 * @param {ReleaseRail} rail
 * @param {string} version
 */
export function githubTagForRail(rail, version) {
  const ver = String(version ?? "").trim();
  if (rail === "android") return `android-v${ver}`;
  return `v${ver}`;
}

/**
 * @param {ReleaseRail} rail
 * @param {string} version
 * @param {string} reason
 */
export function skipDirectFeedWarning({ rail, version, tag, reason }) {
  const why =
    reason === "draft"
      ? "is still a draft"
      : reason === "not-found"
        ? "was not found as a published release (drafts are hidden from the public API)"
        : `could not be confirmed as published (${reason})`;
  return (
    `⚠ GitHub release ${tag} ${why} — uploaded ${rail} v${version} artifacts but skipped latest.json.\n` +
    `  Publish the release, then run: pnpm sync:release-cdn --from-github`
  );
}

/**
 * Direct-path gate: only write latest.json when GitHub has a non-draft release
 * for this version. 404 (typical for drafts on the public API), ``draft: true``,
 * and fetch errors all skip the feed — they must not fail the CLI, or they
 * would abort ``release:win`` / ``release:android``.
 *
 * @param {ReleaseRail} rail
 * @param {string} version
 * @param {{ fetchImpl?: typeof fetch }} [opts]
 */
export async function inspectGithubReleaseForFeed(
  rail,
  version,
  { fetchImpl = fetch } = {},
) {
  const tag = githubTagForRail(rail, version);
  const url = `https://api.github.com/repos/${RELEASES_REPO}/releases/tags/${encodeURIComponent(tag)}`;
  try {
    const res = await fetchImpl(url, { headers: GH_FETCH_HEADERS });
    if (res.status === 404) {
      return { tag, published: false, reason: "not-found" };
    }
    if (!res.ok) {
      return { tag, published: false, reason: `http-${res.status}` };
    }
    const release = await res.json();
    if (release?.draft) {
      return { tag, published: false, reason: "draft", release };
    }
    return { tag, published: true, reason: "published", release };
  } catch (error) {
    return { tag, published: false, reason: "error", error };
  }
}

async function downloadTo(url, dest) {
  const res = await fetch(url, {
    headers: { "User-Agent": "agentcore-sync-release-cdn" },
    redirect: "follow",
  });
  if (!res.ok) throw new Error(`download ${url} → HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  writeFileSync(dest, buf);
  return dest;
}

/**
 * Merge desktop latest.json for a channel: keep the other platform's filename
 * when only win or mac is being synced this run.
 * @param {import("../../apps/website/functions/_lib/downloadsCdn.mjs").DesktopChannel} channel
 * @param {object} nextPartial
 */
async function mergeDesktopLatestJson(channel, nextPartial) {
  const candidates = [desktopLatestJsonUrl(channel)];
  // Migration: first stable sync may only have flat desktop/latest.json.
  if (channel === "stable") {
    candidates.push(cdnUrl(`${DOWNLOADS_DESKTOP_PREFIX}/latest.json`));
  }
  for (const url of candidates) {
    try {
      const prev = await fetchJson(url);
      return buildDesktopLatestJson({
        version: nextPartial.version || prev.version,
        winFilename: nextPartial.winFilename || prev.winFilename,
        macFilename:
          nextPartial.macFilename !== undefined
            ? nextPartial.macFilename
            : prev.macFilename || "",
        releaseNotesUrl: nextPartial.releaseNotesUrl || prev.releaseNotesUrl,
      });
    } catch {
      // try next candidate
    }
  }
  return buildDesktopLatestJson(nextPartial);
}

function collectDesktopNames(version, desktopDir) {
  const winName = winInstallerFilename(version);
  const macName = macDmgFilename(version);
  const candidates = [
    ...desktopArtifactNames(version),
    "latest.yml",
    "latest-mac.yml",
  ];
  const present = candidates.filter((n) => existsSync(join(desktopDir, n)));
  return {
    present,
    hasWin: present.includes(winName),
    hasMac: present.includes(macName),
    winName,
    macName: present.includes(macName) ? macName : "",
  };
}

/**
 * @param {string} version
 * @param {string} desktopDir
 * @param {import("../../apps/website/functions/_lib/downloadsCdn.mjs").DesktopChannel} channel
 */
function syncDesktopDir(version, desktopDir, channel) {
  if (!existsSync(desktopDir)) {
    console.error(`desktop dir not found: ${desktopDir}`);
    process.exit(1);
  }
  const flags = collectDesktopNames(version, desktopDir);
  if (flags.present.length === 0) {
    console.error(`No desktop assets found under ${desktopDir}`);
    process.exit(1);
  }
  if (!flags.hasWin && !flags.hasMac) {
    console.error(
      `Expected win and/or mac installer in ${desktopDir}; got: ${flags.present.join(", ")}`,
    );
    process.exit(1);
  }
  const root = downloadsRoot();
  const dests = desktopSyncDestPrefixes(channel);
  for (const rel of dests) {
    putRemoteDirFiles(desktopDir, `${root}/${rel}`, flags.present);
  }
  console.log(
    `✓ desktop assets → ${dests.map((d) => `${d}/`).join(" + ")} (channel=${channel})`,
  );
  return flags;
}

/**
 * @param {string} version
 * @param {object} flags
 * @param {import("../../apps/website/functions/_lib/downloadsCdn.mjs").DesktopChannel} channel
 */
async function writeDesktopManifest(version, flags, channel) {
  const { hasWin, hasMac, winName, macName } = flags;
  const manifest = await mergeDesktopLatestJson(channel, {
    version,
    ...(hasWin ? { winFilename: winName } : {}),
    ...(hasMac ? { macFilename: macName } : {}),
    releaseNotesUrl: artifactUrlsForVersion(version).releaseNotesUrl,
  });

  if (!manifest.winFilename) {
    console.error("desktop latest.json missing winFilename after merge");
    process.exit(1);
  }

  const tmpDir = mkdtempSync(join(tmpdir(), "ac-cdn-"));
  const tmp = join(tmpDir, "latest.json");
  writeFileSync(tmp, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  const root = downloadsRoot();
  for (const rel of desktopSyncDestPrefixes(channel)) {
    putRemoteFile(tmp, `${root}/${rel}/latest.json`);
  }
  rmSync(tmpDir, { recursive: true, force: true });
  console.log(
    `✓ desktop/${channel} latest.json → v${manifest.version}` +
      (channel === "stable" ? " (+ flat mirror)" : ""),
  );
  console.log(`  win: ${manifest.winFilename}`);
  console.log(`  mac: ${manifest.macFilename || "(none)"}`);
  return manifest;
}

function uploadAndroidApk(version, apkPath) {
  if (!existsSync(apkPath)) {
    console.error(`APK not found: ${apkPath}`);
    process.exit(1);
  }
  const name = basename(apkPath);
  const expected = androidApkFilename(version);
  if (name !== expected) {
    console.warn(`⚠ APK name ${name} ≠ expected ${expected} — uploading as ${name}`);
  }
  const root = downloadsRoot();
  putRemoteFile(apkPath, `${root}/${DOWNLOADS_ANDROID_PREFIX}/${name}`);
  return { name };
}

function writeAndroidManifest(version, name) {
  const manifest = buildAndroidLatestJson({ version, filename: name });
  const tmpDir = mkdtempSync(join(tmpdir(), "ac-cdn-"));
  const tmp = join(tmpDir, "latest.json");
  writeFileSync(tmp, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  const root = downloadsRoot();
  putRemoteFile(tmp, `${root}/${DOWNLOADS_ANDROID_PREFIX}/latest.json`);
  rmSync(tmpDir, { recursive: true, force: true });
  console.log(`✓ android latest.json → v${version} (${name})`);
  return manifest;
}

async function syncAndroidFile(version, apkPath) {
  const { name } = uploadAndroidApk(version, apkPath);
  return writeAndroidManifest(version, name);
}

/**
 * Direct ``--desktop`` / ``--android`` path: always upload artifacts; write
 * latest.json only when GitHub already has a non-draft release for this version.
 *
 * @param {{
 *   rail: ReleaseRail,
 *   version: string,
 *   channel?: string,
 *   desktopDir?: string,
 *   androidPath?: string,
 *   fetchImpl?: typeof fetch,
 *   uploadDesktop?: typeof syncDesktopDir,
 *   writeDesktopFeed?: typeof writeDesktopManifest,
 *   uploadAndroid?: typeof uploadAndroidApk,
 *   writeAndroidFeed?: typeof writeAndroidManifest,
 *   pruneDesktop?: typeof pruneDesktopDests,
 *   pruneAndroid?: typeof pruneAndroidDest,
 *   warn?: (...args: unknown[]) => void,
 * }} opts
 */
export async function runDirectRailSync({
  rail,
  version,
  channel = DESKTOP_CHANNEL_DEFAULT,
  desktopDir = "",
  androidPath = "",
  fetchImpl = fetch,
  uploadDesktop = syncDesktopDir,
  writeDesktopFeed = writeDesktopManifest,
  uploadAndroid = uploadAndroidApk,
  writeAndroidFeed = writeAndroidManifest,
  pruneDesktop = pruneDesktopDests,
  pruneAndroid = pruneAndroidDest,
  warn = (...args) => console.warn(...args),
}) {
  const inspection = await inspectGithubReleaseForFeed(rail, version, {
    fetchImpl,
  });
  const writeFeed = inspection.published;

  if (rail === "desktop") {
    const flags = uploadDesktop(version, desktopDir, channel);
    if (writeFeed) {
      await writeDesktopFeed(version, flags, channel);
    } else {
      warn(
        skipDirectFeedWarning({
          rail,
          version,
          tag: inspection.tag,
          reason: inspection.reason,
        }),
      );
    }
    pruneDesktop(version, channel);
    return { inspection, writeFeed, flags };
  }

  if (rail !== "android") {
    throw new Error(`unknown rail: ${rail}`);
  }
  const uploaded = uploadAndroid(version, androidPath);
  if (writeFeed) {
    await writeAndroidFeed(version, uploaded.name);
  } else {
    warn(
      skipDirectFeedWarning({
        rail,
        version,
        tag: inspection.tag,
        reason: inspection.reason,
      }),
    );
  }
  pruneAndroid(version);
  return { inspection, writeFeed, uploaded };
}

/** @param {string} tag */
function isStableDesktopTag(tag) {
  return /^v\d+\.\d+\.\d+$/i.test(tag);
}

/** @param {string} tag */
function isBetaDesktopTag(tag) {
  return /^v\d+\.\d+\.\d+-.+/i.test(tag) && !tag.startsWith("android-");
}

/**
 * Desktop "latest" on GitHub is NOT always /releases/latest (Android tags can win).
 * Prefer newest matching non-draft tag for the requested channel.
 * @param {import("../../apps/website/functions/_lib/downloadsCdn.mjs").DesktopChannel} channel
 */
async function fetchLatestDesktopGithubRelease(channel) {
  const releases = await fetchJson(
    `https://api.github.com/repos/${RELEASES_REPO}/releases?per_page=30`,
  );
  if (!Array.isArray(releases)) throw new Error("GitHub releases list invalid");
  const tagOk = channel === "beta" ? isBetaDesktopTag : isStableDesktopTag;
  for (const release of releases) {
    if (release.draft) continue;
    const tag = String(release.tag_name ?? "");
    if (!tagOk(tag)) continue;
    const assets = release.assets ?? [];
    const hasDesktop = assets.some(
      (a) =>
        /-win-x64\.exe$/i.test(a.name) ||
        a.name === "latest.yml" ||
        /-mac-arm64\.dmg$/i.test(a.name),
    );
    if (!hasDesktop) continue;
    return release;
  }
  throw new Error(
    `No published desktop ${channel} release found on GitHub`,
  );
}

/**
 * @param {{ desktopOnly: boolean, androidOnly: boolean, channel: string }} opts
 */
async function syncFromGithub({ desktopOnly, androidOnly, channel }) {
  const tmpRoot = mkdtempSync(join(tmpdir(), "ac-cdn-gh-"));
  try {
    if (!androidOnly) {
      const latest = await fetchLatestDesktopGithubRelease(channel);
      const version = String(latest.tag_name).replace(/^v/, "");
      const assets = latest.assets ?? [];
      const dir = join(tmpRoot, "desktop");
      const { mkdirSync } = await import("node:fs");
      mkdirSync(dir, { recursive: true });
      const want = new Set([
        ...desktopArtifactNames(version),
        "latest.yml",
        "latest-mac.yml",
      ]);
      for (const asset of assets) {
        if (!want.has(asset.name)) continue;
        console.log(`→ download GH ${asset.name}`);
        await downloadTo(asset.browser_download_url, join(dir, asset.name));
      }
      const flags = syncDesktopDir(version, dir, channel);
      await writeDesktopManifest(version, flags, channel);
      pruneDesktopDests(version, channel);
    }

    if (!desktopOnly) {
      const releases = await fetchJson(
        `https://api.github.com/repos/${RELEASES_REPO}/releases?per_page=30`,
      );
      if (!Array.isArray(releases)) throw new Error("GitHub releases list invalid");
      let found = null;
      for (const release of releases) {
        if (release.draft) continue;
        const apk = (release.assets ?? []).find((a) =>
          /-android\.apk$/i.test(a.name),
        );
        if (!apk) continue;
        const tag = String(release.tag_name ?? "");
        const version = tag.startsWith("android-v")
          ? tag.slice("android-v".length)
          : tag.replace(/^v/, "");
        found = { version, apk };
        break;
      }
      if (!found) {
        console.warn("⚠ no published Android APK on GitHub — skip android sync");
      } else {
        const dest = join(tmpRoot, found.apk.name);
        console.log(`→ download GH ${found.apk.name}`);
        await downloadTo(found.apk.browser_download_url, dest);
        await syncAndroidFile(found.version, dest);
        pruneAndroidDest(found.version);
      }
    }
  } finally {
    rmSync(tmpRoot, { recursive: true, force: true });
  }
}

function installNginxRemote() {
  requireEnv("DEPLOY_SSH_HOST");
  const conf = join(REPO_ROOT, "deploy/nginx/downloads.conf");
  if (!existsSync(conf)) {
    console.error(`missing ${conf}`);
    process.exit(1);
  }
  scp(conf, "/tmp/downloads.conf");
  const script = readFileSync(
    join(REPO_ROOT, "deploy/scripts/downloads-remote-install.sh"),
    "utf8",
  );
  const host = downloadsHost();
  sshScript(`export DOWNLOADS_HOST=${JSON.stringify(host)}
${script}`);
}

async function main() {
  loadDeployEnv();
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(`Usage:
  pnpm sync:release-cdn --install-nginx
  pnpm sync:release-cdn --desktop <dir> --version <ver> [--channel stable|beta]
  pnpm sync:release-cdn --android <apk> --version <ver>
  pnpm sync:release-cdn --from-github [--channel stable|beta] [--desktop-only|--android-only]
  pnpm sync:release-cdn --prune-only [--channel stable|beta] [--version <ver>] [--prune-dry-run]
  pnpm sync:release-cdn --prune-only --android-only [--version <ver>] [--prune-dry-run]

  --prune-dry-run   list old artifacts that would be deleted (no rm)
  --skip-prune      sync without removing older version files
`);
    process.exit(0);
  }

  if (args.pruneOnly && (args.installNginx || args.fromGithub || args.desktopDir || args.androidPath)) {
    console.error("--prune-only cannot be combined with sync / --install-nginx");
    process.exit(1);
  }
  if (args.skipPrune && (args.pruneDryRun || args.pruneOnly)) {
    console.error("cannot combine --skip-prune with --prune-dry-run / --prune-only");
    process.exit(1);
  }

  pruneMode = {
    dryRun: Boolean(args.pruneDryRun),
    skip: Boolean(args.skipPrune),
  };

  // Touch SSH early so missing keys fail before long GH downloads.
  requireEnv("DEPLOY_SSH_HOST");
  requireEnv("DEPLOY_SSH_USER");

  if (args.installNginx) {
    installNginxRemote();
    return;
  }

  const channel = normalizeDesktopChannel(
    /** @type {string} */ (args.channel || DESKTOP_CHANNEL_DEFAULT),
  );

  if (args.pruneOnly) {
    const version = String(args.version || "").trim();
    if (args.androidOnly) {
      console.log(
        `→ prune android` +
          (version ? ` version=${version}` : " (version from latest.json)") +
          (pruneMode.dryRun ? " [dry-run]" : ""),
      );
      pruneAndroidDest(version);
    } else {
      console.log(
        `→ prune desktop channel=${channel}` +
          (version ? ` version=${version}` : " (version from latest.json)") +
          (pruneMode.dryRun ? " [dry-run]" : ""),
      );
      pruneDesktopDests(version, channel);
    }
    console.log("✓ prune complete");
    return;
  }

  if (args.fromGithub) {
    console.log(
      `→ sync from GitHub → ${downloadsRoot()} @ ${downloadsHost()} (channel=${channel})`,
    );
    await syncFromGithub({
      desktopOnly: Boolean(args.desktopOnly),
      androidOnly: Boolean(args.androidOnly),
      channel,
    });
    console.log(
      `✓ CDN sync complete → ${cdnUrl(desktopChannelPrefix(channel))}/`,
    );
    return;
  }

  if (!args.desktopDir && !args.androidPath) {
    console.error(
      "usage: pnpm sync:release-cdn --desktop <dir> --version <ver> [--channel stable|beta]\n" +
        "       pnpm sync:release-cdn --android <apk> --version <ver>\n" +
        "       pnpm sync:release-cdn --from-github [--channel stable|beta]\n" +
        "       pnpm sync:release-cdn --prune-only [--channel stable|beta] [--prune-dry-run]\n" +
        "       pnpm sync:release-cdn --install-nginx",
    );
    process.exit(1);
  }
  if (!args.version) {
    console.error("Missing --version");
    process.exit(1);
  }

  if (args.desktopDir) {
    await runDirectRailSync({
      rail: "desktop",
      version: /** @type {string} */ (args.version),
      channel,
      desktopDir: /** @type {string} */ (args.desktopDir),
    });
  }
  if (args.androidPath) {
    if (channel === "beta") {
      console.warn(
        "⚠ --android ignores --channel (android rail unchanged); writing android/",
      );
    }
    await runDirectRailSync({
      rail: "android",
      version: /** @type {string} */ (args.version),
      androidPath: /** @type {string} */ (args.androidPath),
    });
  }
  console.log(`✓ CDN sync complete → ${cdnUrl("")}`);
}

function isCliEntry() {
  const entry = process.argv[1];
  if (!entry) return false;
  return import.meta.url === pathToFileURL(resolvePath(entry)).href;
}

if (isCliEntry()) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
