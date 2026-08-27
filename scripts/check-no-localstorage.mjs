#!/usr/bin/env node
/**
 * Ban direct `localStorage` in the desktop renderer (except the unified
 * uiStorage module + allowlisted dev-only / non-UI callers).
 *
 * Usage:
 *   node scripts/check-no-localstorage.mjs --src apps/desktop/src/renderer
 */
import { readdir, readFile } from "node:fs/promises";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");

/** Relative posix-ish paths (from --src root) that may touch localStorage. */
const ALLOWLIST = new Set([
  // Unified storage backend — the only production UI persistence entrypoint.
  "lib/uiStorage.ts",
  // Dev-only console probes (explicitly left on raw localStorage).
  "services/sseTrace.ts",
  "services/turnTrace.ts",
]);

const USAGE_RE = /\blocalStorage\b/;

function parseArgs(argv) {
  let src = "";
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--src" && argv[i + 1]) {
      src = argv[++i];
    }
  }
  if (!src) {
    console.error("usage: check-no-localstorage.mjs --src <directory>");
    process.exit(2);
  }
  return { srcDir: join(ROOT, src) };
}

async function walk(dir) {
  /** @type {string[]} */
  const out = [];
  for (const name of await readdir(dir, { withFileTypes: true })) {
    const p = join(dir, name.name);
    if (name.isDirectory()) {
      if (
        name.name === "node_modules" ||
        name.name === "out" ||
        name.name === "dist" ||
        name.name === "__tests__"
      ) {
        continue;
      }
      out.push(...(await walk(p)));
    } else if (/\.(tsx|ts)$/.test(name.name) && !name.name.endsWith(".test.ts")) {
      out.push(p);
    }
  }
  return out;
}

function toPosixRel(srcDir, file) {
  return relative(srcDir, file).split(sep).join("/");
}

const { srcDir } = parseArgs(process.argv);

/** @type {{ file: string; line: number; text: string }[]} */
const violations = [];

for (const file of await walk(srcDir)) {
  const rel = toPosixRel(srcDir, file);
  if (ALLOWLIST.has(rel)) continue;
  // Skip comment-only / JSDoc mentions by requiring a code-ish neighbor, but
  // keep it simple: any identifier hit outside allowlist fails (comments that
  // say "localStorage" in migrated modules should be reworded).
  const content = await readFile(file, "utf8");
  const lines = content.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    // Ignore pure comments / JSDoc.
    if (
      trimmed.startsWith("//") ||
      trimmed.startsWith("*") ||
      trimmed.startsWith("/*")
    ) {
      continue;
    }
    if (USAGE_RE.test(line)) {
      violations.push({
        file: relative(ROOT, file),
        line: i + 1,
        text: trimmed.slice(0, 120),
      });
    }
  }
}

if (violations.length === 0) {
  console.log(`check-no-localstorage: OK (${relative(ROOT, srcDir)})`);
  process.exit(0);
}

console.error(`check-no-localstorage: ${violations.length} violation(s)\n`);
console.error(
  "Use @/lib/uiStorage (uiGet/uiSet/createZustandUiStorage) instead of raw localStorage.\n",
);
for (const v of violations) {
  console.error(`${v.file}:${v.line}`);
  console.error(`  ${v.text}\n`);
}
process.exit(1);
