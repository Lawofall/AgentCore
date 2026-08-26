#!/usr/bin/env node
/**
 * Android APK release: public Capacitor CORS preflight → sync version →
 * Vite build → cap sync → signed assembleRelease → `gh release upload`
 * to Lawofall/AgentCore-releases.
 *
 *   pnpm -C apps/mobile release:android
 *   pnpm -C apps/mobile release:android -- --skip-draft   # draft already exists
 *
 * Prerequisites:
 *   - Android SDK (`android/local.properties` sdk.dir=… or ANDROID_HOME)
 *   - `android/keystore.properties` (see keystore.properties.example) — required;
 *     this script refuses to ship an unsigned release APK
 *   - `gh auth login` with write access to Lawofall/AgentCore-releases
 *   - GH_TOKEN or `gh auth token` available
 *   - VITE_API_URL / AGENTCORE_APP_API_URL / AGENTCORE_APP_HOST (same as deploy-pages)
 *
 * Tag track: `android-v<ver>` (separate from desktop `v<ver>`).
 * Asset: `AgentCore-<ver>-android.apk`
 */
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertCapacitorCors } from "../../../deploy/scripts/check-capacitor-cors.mjs";
import {
  REPO_ROOT,
  loadDeployEnv,
  run,
} from "../../../deploy/scripts/load-deploy-env.mjs";
import {
  readMobileVersion,
  syncAndroidVersion,
} from "./sync-android-version.mjs";

const __dir = dirname(fileURLToPath(import.meta.url));
const MOBILE_DIR = join(__dir, "..");
const ANDROID_DIR = join(MOBILE_DIR, "android");
const RELEASES_REPO = "Lawofall/AgentCore-releases";

const skipDraft = process.argv.includes("--skip-draft");

function apkAssetName(version) {
  return `AgentCore-${version}-android.apk`;
}

function gh(args, { allowFail = false, capture = false } = {}) {
  const result = spawnSync("gh", args, {
    cwd: REPO_ROOT,
    stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: capture ? "utf8" : undefined,
    shell: false,
    env: process.env,
  });
  if (!allowFail && result.status !== 0) {
    if (capture && result.stderr) process.stderr.write(result.stderr);
    process.exit(result.status ?? 1);
  }
  return capture
    ? {
        ok: result.status === 0,
        stdout: result.stdout ?? "",
        stderr: result.stderr ?? "",
      }
    : result.status === 0;
}

function ensureGhToken() {
  if (process.env.GH_TOKEN?.trim() || process.env.GITHUB_TOKEN?.trim()) return;
  const result = spawnSync("gh", ["auth", "token"], {
    encoding: "utf8",
    shell: false,
  });
  if (result.status === 0 && result.stdout?.trim()) {
    process.env.GH_TOKEN = result.stdout.trim();
    console.log("→ GH_TOKEN from gh auth token");
    return;
  }
  console.error("Missing GH_TOKEN — run `gh auth login` or export GH_TOKEN");
  process.exit(1);
}

function ensureKeystore() {
  const props = join(ANDROID_DIR, "keystore.properties");
  if (existsSync(props)) return;
  console.error("");
  console.error("Missing android/keystore.properties — refusing to build an unsigned release APK.");
  console.error(
    "Copy android/keystore.properties.example → android/keystore.properties",
  );
  console.error("and fill storeFile / storePassword / keyAlias / keyPassword.");
  console.error("");
  process.exit(1);
}

function ensureAndroidSdk() {
  const localProps = join(ANDROID_DIR, "local.properties");
  const hasLocal = existsSync(localProps);
  const hasEnv = Boolean(
    process.env.ANDROID_HOME?.trim() || process.env.ANDROID_SDK_ROOT?.trim(),
  );
  if (hasLocal || hasEnv) return;
  console.error("");
  console.error("Android SDK not found.");
  console.error(
    "  • Create apps/mobile/android/local.properties with sdk.dir=<SDK path>",
  );
  console.error("  • or set ANDROID_HOME / ANDROID_SDK_ROOT");
  console.error("Install Android Studio / command-line tools, then retry.");
  console.error("");
  process.exit(1);
}

function ensureDraftRelease(tag) {
  if (skipDraft) {
    console.log(
      `→ skip draft (--skip-draft); expecting ${tag} on ${RELEASES_REPO}`,
    );
    return;
  }
  console.log(`→ ensure draft release ${tag} on ${RELEASES_REPO}`);
  if (
    gh(["release", "view", tag, "--repo", RELEASES_REPO], { allowFail: true })
  ) {
    console.log(`  draft/release ${tag} already exists`);
    return;
  }
  gh([
    "release",
    "create",
    tag,
    "--repo",
    RELEASES_REPO,
    "--draft",
    "--title",
    tag,
    "--notes",
    "Android APK release (draft — publish after native-surface smoke, or immediately for renderer/protocol-only).",
  ]);
}

function resolveApiUrl() {
  const APP_HOST = process.env.AGENTCORE_APP_HOST || "app.fashitianxia.xyz";
  return (
    process.env.AGENTCORE_APP_API_URL ||
    process.env.VITE_API_URL ||
    `https://${APP_HOST}/api`
  );
}

function assembleRelease() {
  const gradlew =
    process.platform === "win32" ? "gradlew.bat" : "./gradlew";
  const gradlewPath = join(ANDROID_DIR, gradlew);
  if (!existsSync(gradlewPath) && !existsSync(join(ANDROID_DIR, "gradlew"))) {
    console.error(`Missing Gradle wrapper under ${ANDROID_DIR}`);
    process.exit(1);
  }
  run("gradle assembleRelease", gradlew, ["assembleRelease"], {
    cwd: ANDROID_DIR,
    env: process.env,
  });
}

function stageApk(version) {
  const built = join(
    ANDROID_DIR,
    "app",
    "build",
    "outputs",
    "apk",
    "release",
    "app-release.apk",
  );
  if (!existsSync(built)) {
    console.error(`Missing release APK: ${built}`);
    console.error(
      "Gradle assembleRelease finished without app-release.apk — check signing / SDK errors above.",
    );
    process.exit(1);
  }
  const outDir = join(MOBILE_DIR, "release", version);
  mkdirSync(outDir, { recursive: true });
  const name = apkAssetName(version);
  const dest = join(outDir, name);
  copyFileSync(built, dest);
  console.log(`→ staged ${dest}`);
  return dest;
}

function uploadAndVerify(tag, version, path) {
  const name = apkAssetName(version);
  console.log(`→ gh release upload ${tag} --clobber`);
  gh([
    "release",
    "upload",
    tag,
    path,
    "--repo",
    RELEASES_REPO,
    "--clobber",
  ]);

  const { stdout } = gh(
    ["release", "view", tag, "--repo", RELEASES_REPO, "--json", "assets"],
    { capture: true },
  );
  let assets;
  try {
    assets = JSON.parse(stdout).assets ?? [];
  } catch (err) {
    console.error(`Failed to parse gh release view JSON: ${err}`);
    process.exit(1);
  }
  const present = new Set(assets.map((a) => a.name));
  if (!present.has(name)) {
    console.error(`Release ${tag} missing asset: ${name}`);
    console.error(`Present: ${[...present].join(", ") || "(none)"}`);
    process.exit(1);
  }
  console.log(`✓ remote assets verified on ${tag}: ${name}`);
}

async function main() {
  loadDeployEnv();
  ensureKeystore();
  ensureAndroidSdk();
  ensureGhToken();

  const version = readMobileVersion();
  const tag = `android-v${version}`;
  const apiUrl = resolveApiUrl();
  const googleServices = join(ANDROID_DIR, "app", "google-services.json");
  const pushEnabled = existsSync(googleServices);
  console.log(`→ android release ${version}`);
  console.log(`  VITE_API_URL=${apiUrl}`);
  await assertCapacitorCors({ apiBaseUrl: apiUrl });
  console.log(
    `  VITE_PUSH_ENABLED=${pushEnabled} (google-services.json ${pushEnabled ? "found" : "missing — push gated off to avoid native crash"})`,
  );
  ensureDraftRelease(tag);

  syncAndroidVersion(version);

  const buildEnv = {
    ...process.env,
    VITE_API_URL: apiUrl,
    // Never call PushNotifications.register without Firebase config — process crash.
    VITE_PUSH_ENABLED: pushEnabled ? "true" : "false",
  };

  run("pnpm build", "pnpm", ["build"], {
    cwd: MOBILE_DIR,
    env: buildEnv,
  });
  run("cap sync android", "pnpm", ["exec", "cap", "sync", "android"], {
    cwd: MOBILE_DIR,
    env: buildEnv,
  });

  assembleRelease();
  const apkPath = stageApk(version);
  uploadAndVerify(tag, version, apkPath);

  // Windows: run() uses shell:true and breaks on spaces in process.execPath (e.g. Program Files).
  console.log("→ sync:release-cdn (android)");
  const syncResult = spawnSync(
    process.execPath,
    [
      join(REPO_ROOT, "deploy/scripts/sync-release-cdn.mjs"),
      "--android",
      apkPath,
      "--version",
      version,
    ],
    { cwd: REPO_ROOT, stdio: "inherit", env: process.env, shell: false },
  );
  if (syncResult.status !== 0) process.exit(syncResult.status ?? 1);

  console.log("");
  console.log(`✓ Android release ${tag} built and uploaded to ${RELEASES_REPO}`);
  console.log(`  local: ${apkPath}`);
  console.log(`  CDN: https://downloads.fashitianxia.xyz/android/`);
  console.log("");
  console.log(
    "Publish now unless this wave touched native surface (android/, Capacitor, CORS, shell auth/SSE).",
  );
  console.log(
    `  gh release edit ${tag} --repo ${RELEASES_REPO} --draft=false`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
