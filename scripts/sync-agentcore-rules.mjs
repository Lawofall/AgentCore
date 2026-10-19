#!/usr/bin/env node
/**
 * Mirror `.cursor/rules/*.mdc` → `AgentCore/rules/*.md` (product discovery path).
 *
 * Canonical SoT: `.cursor/rules/*.mdc` (git). Dest is gitignored (`/AgentCore/`).
 * Host names: StrReplace / Read / Task → edit / read / delegate;
 * `globs` + `alwaysApply` → `apply` / `paths`.
 *
 * Usage:
 *   pnpm sync:rules
 *   pnpm sync:rules:check
 *
 * Wired: package.json; release:gate contracts 段；CI contracts job。
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_DIR = path.join(ROOT, ".cursor", "rules");
const DEST_DIR = path.join(ROOT, "AgentCore", "rules");
const GENERATED_MARK = "generated from .cursor/rules/";

export function parseCursorMdc(text) {
  const trimmed = text.replace(/^\uFEFF/, "");
  if (!trimmed.startsWith("---")) {
    throw new Error("missing opening frontmatter fence");
  }
  const afterOpen = trimmed.slice(3).replace(/^\r?\n/, "");
  const close = afterOpen.search(/^---\s*$/m);
  if (close < 0) {
    throw new Error("unclosed frontmatter");
  }
  const fm = afterOpen.slice(0, close);
  const body = afterOpen.slice(close).replace(/^---[ \t]*\r?\n?/, "");

  let description = "";
  let alwaysApply = false;
  const globs = [];
  let inGlobs = false;
  for (const raw of fm.split(/\r?\n/)) {
    if (inGlobs) {
      const item = raw.match(/^\s+-\s+["']?(.+?)["']?\s*$/);
      if (item) {
        globs.push(item[1]);
        continue;
      }
      inGlobs = false;
    }
    const desc = raw.match(/^description:\s*(.*)$/);
    if (desc) {
      description = desc[1].trim();
      continue;
    }
    const always = raw.match(/^alwaysApply:\s*(true|false)\s*$/);
    if (always) {
      alwaysApply = always[1] === "true";
      continue;
    }
    const globInline = raw.match(/^globs:\s+(.+)$/);
    if (globInline) {
      globs.push(globInline[1].trim().replace(/^["']|["']$/g, ""));
      continue;
    }
    if (/^globs:\s*$/.test(raw)) {
      inGlobs = true;
    }
  }
  return { description, alwaysApply, globs, body };
}

export function rewriteHostTools(body) {
  return body
    .replaceAll("Cursor Task 官方说明", "`delegate`")
    .replaceAll("Cursor Task", "delegate")
    .replaceAll("StrReplace", "edit")
    .replaceAll("AwaitShell", "等命令结束")
    .replaceAll("**Read**", "`read`")
    .replaceAll("`Read`", "`read`")
    .replace(/`([a-z0-9-]+)\.mdc`/g, "`$1`");
}

export function applyMode(parsed) {
  if (parsed.alwaysApply) {
    return { apply: "always", paths: [] };
  }
  if (parsed.globs.length) {
    return { apply: "paths", paths: parsed.globs };
  }
  return { apply: "on_demand", paths: [] };
}

export function renderAgentCoreMarkdown(stem, parsed) {
  const { apply, paths } = applyMode(parsed);
  const body = rewriteHostTools(parsed.body).replace(/^\uFEFF/, "");
  const lines = ["---", `apply: ${apply}`];
  if (parsed.description) {
    lines.push(`description: ${parsed.description}`);
  }
  if (paths.length) {
    lines.push(`paths: ${paths.join(", ")}`);
  }
  lines.push(`# ${GENERATED_MARK}${stem}.mdc — pnpm sync:rules; do not edit`);
  lines.push("---");
  const fm = `${lines.join("\n")}\n`;
  return body.length ? `${fm}${body.replace(/^\r?\n/, "")}` : fm;
}

export function listSourceStems(sourceDir = SOURCE_DIR) {
  return readdirSync(sourceDir)
    .filter((name) => name.endsWith(".mdc") && !name.startsWith("."))
    .map((name) => name.slice(0, -4))
    .sort();
}

export function transformAll(sourceDir = SOURCE_DIR) {
  const files = {};
  for (const stem of listSourceStems(sourceDir)) {
    const raw = readFileSync(path.join(sourceDir, `${stem}.mdc`), "utf8");
    files[`${stem}.md`] = renderAgentCoreMarkdown(stem, parseCursorMdc(raw));
  }
  return files;
}

function isGeneratedMarkdown(content) {
  return content.includes(`# ${GENERATED_MARK}`);
}

export function destDiff(expected, destDir = DEST_DIR) {
  /** @type {{ missing: string[], extra: string[], changed: string[] }} */
  const result = { missing: [], extra: [], changed: [] };
  let existing = [];
  try {
    existing = readdirSync(destDir).filter((name) => name.endsWith(".md"));
  } catch (err) {
    if (err && err.code === "ENOENT") {
      result.missing = Object.keys(expected).sort();
      return result;
    }
    throw err;
  }
  const expectedNames = new Set(Object.keys(expected));
  for (const name of Object.keys(expected).sort()) {
    const destPath = path.join(destDir, name);
    let current;
    try {
      current = readFileSync(destPath, "utf8");
    } catch (err) {
      if (err && err.code === "ENOENT") {
        result.missing.push(name);
        continue;
      }
      throw err;
    }
    if (current.replace(/\r\n/g, "\n") !== expected[name].replace(/\r\n/g, "\n")) {
      result.changed.push(name);
    }
  }
  for (const name of existing.sort()) {
    if (expectedNames.has(name)) continue;
    const current = readFileSync(path.join(destDir, name), "utf8");
    if (isGeneratedMarkdown(current)) {
      result.extra.push(name);
    }
  }
  return result;
}

export function writeMirror(expected, destDir = DEST_DIR) {
  mkdirSync(destDir, { recursive: true });
  const diff = destDiff(expected, destDir);
  for (const name of [...diff.missing, ...diff.changed]) {
    writeFileSync(path.join(destDir, name), expected[name], "utf8");
  }
  for (const name of diff.extra) {
    rmSync(path.join(destDir, name));
  }
  return diff;
}

function hasDiff(diff) {
  return diff.missing.length || diff.extra.length || diff.changed.length;
}

function formatDiff(diff) {
  const bits = [];
  if (diff.missing.length) bits.push(`missing ${diff.missing.join(", ")}`);
  if (diff.changed.length) bits.push(`stale ${diff.changed.join(", ")}`);
  if (diff.extra.length) bits.push(`extra ${diff.extra.join(", ")}`);
  return bits.join("; ");
}

export function syncRules({ check = false, sourceDir = SOURCE_DIR, destDir = DEST_DIR } = {}) {
  const expected = transformAll(sourceDir);
  const diff = destDiff(expected, destDir);
  const destMissing =
    diff.missing.length === Object.keys(expected).length &&
    !diff.changed.length &&
    !diff.extra.length;
  if (check) {
    if (destMissing) {
      return { ok: true, destMissing: true, count: Object.keys(expected).length };
    }
    if (hasDiff(diff)) {
      return { ok: false, destMissing: false, diff, count: Object.keys(expected).length };
    }
    return { ok: true, destMissing: false, count: Object.keys(expected).length };
  }
  writeMirror(expected, destDir);
  return { ok: true, destMissing: false, diff, count: Object.keys(expected).length };
}

function isMain() {
  const here = fileURLToPath(import.meta.url);
  const argv1 = process.argv[1] ? path.resolve(process.argv[1]) : "";
  return path.normalize(here) === path.normalize(argv1);
}

function runSelfTest() {
  const testFile = path.join(ROOT, "scripts", "sync-agentcore-rules.test.mjs");
  const result = spawnSync(process.execPath, ["--test", testFile], {
    cwd: ROOT,
    stdio: "inherit",
  });
  return result.status === null ? 1 : result.status;
}

function main() {
  const check = process.argv.includes("--check");
  if (check) {
    const testStatus = runSelfTest();
    if (testStatus !== 0) process.exit(testStatus);
  }
  const result = syncRules({ check });
  if (check) {
    if (!result.ok) {
      console.error(
        `AgentCore/rules 与 .cursor/rules 不一致：${formatDiff(result.diff)}。跑 pnpm sync:rules。`,
      );
      process.exit(1);
    }
    if (result.destMissing) {
      console.log(
        `AgentCore/rules 不在（gitignore）。${result.count} 篇源文件变换通过。用 AgentCore 开发本仓时跑 pnpm sync:rules。`,
      );
    } else {
      console.log(`AgentCore/rules 与 .cursor/rules 对齐（${result.count} 篇）。`);
    }
    return;
  }
  console.log(`Wrote ${result.count} rules → AgentCore/rules/`);
}

if (isMain()) {
  main();
}
