#!/usr/bin/env node
/**
 * Build the desktop webapp into dist-web and publish index.html for Capacitor.
 * Android APK version stays apps/mobile/package.json (AGENTCORE_CLIENT_VERSION).
 */
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readMobileVersion } from "./sync-android-version.mjs";

const MOBILE_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const DESKTOP_DIR = join(MOBILE_DIR, "..", "desktop");
const DIST = join(DESKTOP_DIR, "dist-web");
const version = readMobileVersion();

const result = spawnSync(
  "pnpm",
  ["--filter", "agentcore-desktop", "build:webapp"],
  {
    cwd: join(MOBILE_DIR, "..", ".."),
    stdio: "inherit",
    env: {
      ...process.env,
      AGENTCORE_CLIENT_VERSION: version,
    },
    shell: process.platform === "win32",
  },
);
if (result.status !== 0) process.exit(result.status ?? 1);

const from = join(DIST, "index.webapp.html");
const to = join(DIST, "index.html");
if (!existsSync(from)) {
  console.error(`Missing ${from}`);
  process.exit(1);
}
copyFileSync(from, to);
console.log(`→ Capacitor webDir ready (${DIST}, version ${version})`);
