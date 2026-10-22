/**
 * Content id for the packed gVisor guest rootfs.
 *
 * Same file list and GNU ``sha256sum`` line format the remote prepare script
 * hashes. Changing any input rebuilds ``guest-base:<id>``; api:<sha> then
 * COPY --from that image instead of re-downloading Chromium.
 *
 *   node deploy/scripts/guest-base-id.mjs
 *   node --test deploy/scripts/guest-base-id.test.mjs
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const GUEST_INPUTS = Object.freeze([
  "Dockerfile.guest",
  "build-pins.env",
  "agentcore/tools/sandbox/cloud_python.txt",
]);

/**
 * @param {string} serverRoot apps/server
 * @returns {string} 16-hex id
 */
export function guestBaseId(serverRoot) {
  const listing = GUEST_INPUTS.map((rel) => {
    const body = readFileSync(join(serverRoot, rel));
    const hex = createHash("sha256").update(body).digest("hex");
    return `${hex}  ${rel}\n`;
  }).join("");
  return createHash("sha256").update(listing).digest("hex").slice(0, 16);
}

/**
 * @param {string} serverRoot apps/server
 * @returns {Record<string, string>}
 */
export function readBuildPins(serverRoot) {
  const text = readFileSync(join(serverRoot, "build-pins.env"), "utf8");
  /** @type {Record<string, string>} */
  const pins = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) continue;
    pins[trimmed.slice(0, eq)] = trimmed.slice(eq + 1);
  }
  return pins;
}

const isMain =
  Boolean(process.argv[1]) &&
  pathToFileURL(resolve(process.argv[1])).href === import.meta.url;

if (isMain) {
  const serverRoot = join(dirname(fileURLToPath(import.meta.url)), "../../apps/server");
  process.stdout.write(`${guestBaseId(serverRoot)}\n`);
}
