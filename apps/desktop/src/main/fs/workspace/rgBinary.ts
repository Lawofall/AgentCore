/**
 * Resolve the embedded ripgrep binary for desktop main-process grep.
 * Never falls back to PATH — missing binary is an explicit failure upstream.
 */
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { app } from "electron";

function exeName(): string {
  return process.platform === "win32" ? "rg.exe" : "rg";
}

/** @internal exported for tests */
export function resolveRgBinary(): string | null {
  const env = (process.env.AGENTCORE_RG_PATH ?? "").trim();
  if (env && existsSync(env)) return env;

  const name = exeName();

  // Packaged: electron-builder extraResources → resources/rg/rg[.exe]
  try {
    if (app.isPackaged) {
      const packaged = join(process.resourcesPath, "rg", name);
      if (existsSync(packaged)) return packaged;
      return null;
    }
  } catch {
    // app may be unavailable in unit tests
  }

  // Dev: apps/desktop/resources/rg after `--install-desktop`.
  try {
    const appPath = app.getAppPath();
    const devDesktop = join(appPath, "resources", "rg", name);
    if (existsSync(devDesktop)) return devDesktop;
  } catch {
    // fall through to import.meta path
  }

  // Source-relative fallback (vitest / early boot)
  const here = dirname(fileURLToPath(import.meta.url));
  const fromSrc = join(here, "..", "..", "..", "..", "resources", "rg", name);
  if (existsSync(fromSrc)) return fromSrc;

  return null;
}
