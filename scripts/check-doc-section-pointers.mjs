#!/usr/bin/env node
/**
 * Stale Markdown chapter-pointer gate.
 *
 * Validates only unambiguous compound pointers of the form
 * `§<Chinese numeral> <chapter title>` whose target file is determined
 * without guessing. Anything else is skipped — miss rather than false-alarm.
 *
 * Target headings are `## <num>、<title>` only (not `§9.12` / `§7.6a`
 * decision ids, not `§一·foo` mid-dot nicknames).
 *
 * Usage:
 *   node scripts/check-doc-section-pointers.mjs
 *   node scripts/check-doc-section-pointers.mjs --list
 *   node scripts/check-doc-section-pointers.mjs --verbose
 *
 * Wired: package.json `check:doc-sections`; CI contracts job;
 * `release:gate` contracts section.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const THIS_FILE = fileURLToPath(import.meta.url);

const argv = new Set(process.argv.slice(2));
const VERBOSE = argv.has("--verbose");
const LIST = argv.has("--list");

/** Verbose logging is off during in-memory self-check. */
let emitLog = false;

/** Two-digit 十一 / 十二 before bare 十. Does not match 四·附 / 四A. */
const CN_NUM =
  "(?:[二三四五六七八九]十[一二三四五六七八九]?|十[一二三四五六七八九]|[一二三四五六七八九十])";
const POINTER_RE = new RegExp(`§(${CN_NUM})`, "g");
const HEADING_RE = new RegExp(`^## (${CN_NUM})、\\s*(.+?)\\s*$`);
const MD_LINK_RE = /\[([^\]]*)\]\(([^)]+)\)/g;

const SKIP_DIRS = new Set([
  ".git",
  ".venv",
  "__pycache__",
  "node_modules",
  "dist",
  "out",
  "build",
  "coverage",
  "release",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  "Library",
  "Temp",
  "Logs",
  "site-packages",
  "06-规划",
]);

const SOURCE_EXT = new Set([".md", ".mdc", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".py"]);

const PATH_CHAR_RE = /[A-Za-z0-9._\u4e00-\u9fff\-/]/;

/**
 * @typedef {{ num: string, title: string }} Heading
 * @typedef {{ rel: string, stem: string, headings: Heading[] }} DocEntry
 * @typedef {{ file: string, line: number, pointer: string, target: string, expected: string, actual: string }} Finding
 */

function toPosixRel(abs) {
  return relative(ROOT, abs).split(sep).join("/");
}

function stripHeadingDecor(title) {
  return title
    .replace(/\s*\{#[^}]+\}\s*$/, "")
    .replace(/\s*[✅⏳].*$/, "")
    .replace(/\s+#+\s*$/, "")
    .trim();
}

function isTitleBoundary(rest, len) {
  if (len >= rest.length) return true;
  const next = rest[len];
  return !/[\p{L}\p{N}_]/u.test(next);
}

/** Longest heading title that is a bounded prefix of `rest`. */
function matchHeading(rest, headings) {
  let best = null;
  for (const h of headings) {
    if (!h.title || !rest.startsWith(h.title)) continue;
    if (!isTitleBoundary(rest, h.title.length)) continue;
    if (!best || h.title.length > best.title.length) best = h;
  }
  return best;
}

function parseHeadings(content) {
  /** @type {Heading[]} */
  const headings = [];
  let inFence = false;
  for (const raw of content.split(/\r?\n/)) {
    const fence = raw.trimStart();
    if (fence.startsWith("```") || fence.startsWith("~~~")) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = raw.match(HEADING_RE);
    if (!m) continue;
    const title = stripHeadingDecor(m[2]);
    if (title) headings.push({ num: m[1], title });
  }
  return headings;
}

function walkFiles(dir, acc = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const ent of entries) {
    const abs = join(dir, ent.name);
    if (ent.isDirectory()) {
      if (SKIP_DIRS.has(ent.name)) continue;
      walkFiles(abs, acc);
      continue;
    }
    if (!ent.isFile()) continue;
    const dot = ent.name.lastIndexOf(".");
    const ext = dot >= 0 ? ent.name.slice(dot) : "";
    if (!SOURCE_EXT.has(ext)) continue;
    if (ent.name.endsWith(".generated.ts")) continue;
    if (abs === THIS_FILE) continue;
    acc.push(abs);
  }
  return acc;
}

function buildDocsIndex() {
  /** @type {Map<string, DocEntry>} */
  const byRel = new Map();
  /** @type {Map<string, string | "ambiguous">} */
  const byStem = new Map();
  const docsRoot = join(ROOT, "docs");
  for (const abs of walkFiles(docsRoot)) {
    if (!abs.endsWith(".md")) continue;
    const rel = toPosixRel(abs);
    const stem = abs.slice(abs.lastIndexOf(sep) + 1).replace(/\.md$/i, "");
    const headings = parseHeadings(readFileSync(abs, "utf8"));
    byRel.set(rel, { rel, stem, headings });
    const prev = byStem.get(stem);
    if (prev && prev !== rel) byStem.set(stem, "ambiguous");
    else if (!prev) byStem.set(stem, rel);
  }
  return { byRel, byStem };
}

function resolveDocsHref(href, fromAbs) {
  let raw = href.trim();
  const hash = raw.indexOf("#");
  if (hash >= 0) raw = raw.slice(0, hash);
  raw = raw.replace(/\\/g, "/").replace(/^\.\//, "");
  if (!raw.toLowerCase().endsWith(".md")) return null;
  let rel;
  if (raw.startsWith("/")) rel = raw.slice(1);
  else if (raw.startsWith("docs/")) rel = raw;
  else rel = toPosixRel(join(dirname(fromAbs), raw));
  if (!rel || rel.startsWith("..") || rel.includes("docs/06-规划/")) return null;
  return rel;
}

/**
 * @param {Map<string, DocEntry>} byRel
 * @param {Map<string, string | "ambiguous">} byStem
 */
function resolveToken(token, fromAbs, byRel, byStem) {
  if (!token || token.length < 2) return null;
  if (token.toLowerCase().endsWith(".md") || token.includes("/")) {
    const rel = resolveDocsHref(token, fromAbs);
    return rel && byRel.has(rel) ? rel : null;
  }
  const hit = byStem.get(token);
  if (!hit || hit === "ambiguous") return null;
  return hit;
}

function lookbehindTarget(line, at, fromAbs, byRel, byStem) {
  let i = at - 1;
  while (i >= 0 && (line[i] === " " || line[i] === "\t" || line[i] === "\u3000")) i--;
  if (i >= 0 && line[i] === "`") i--;
  if (i >= 2 && line.slice(i - 2, i + 1).toLowerCase() === ".md") i -= 3;
  if (i >= 0 && line[i] === "`") i--;
  const end = i + 1;
  while (i >= 0 && PATH_CHAR_RE.test(line[i])) i--;
  const token = line.slice(i + 1, end);
  return resolveToken(token, fromAbs, byRel, byStem);
}

function linkTargetAt(line, pos, fromAbs, byRel) {
  MD_LINK_RE.lastIndex = 0;
  let m;
  while ((m = MD_LINK_RE.exec(line))) {
    const textStart = m.index + 1;
    const textEnd = textStart + m[1].length;
    if (pos >= textStart && pos < textEnd) {
      const rel = resolveDocsHref(m[2], fromAbs);
      return rel && byRel.has(rel) ? rel : null;
    }
  }
  return null;
}

function tableRowTarget(line, fromAbs, byRel) {
  if (!line.trimStart().startsWith("|")) return null;
  MD_LINK_RE.lastIndex = 0;
  const files = new Set();
  let m;
  while ((m = MD_LINK_RE.exec(line))) {
    const rel = resolveDocsHref(m[2], fromAbs);
    if (rel && byRel.has(rel)) files.add(rel);
  }
  return files.size === 1 ? [...files][0] : null;
}

function intraDocTarget(sourceRel, rest, byRel) {
  if (!sourceRel.startsWith("docs/") || !sourceRel.endsWith(".md")) return null;
  const doc = byRel.get(sourceRel);
  if (!doc) return null;
  return matchHeading(rest, doc.headings) ? sourceRel : null;
}

function headingLine(h) {
  return `## ${h.num}、${h.title}`;
}

/**
 * @param {string} line
 * @param {string} sourceRel
 * @param {string} fromAbs
 * @param {{ byRel: Map<string, DocEntry>, byStem: Map<string, string | "ambiguous"> }} index
 * @param {number} lineNo
 * @param {Finding[]} findings
 * @param {{ checked: number, skipped: number }} stats
 */
function checkLine(line, sourceRel, fromAbs, index, lineNo, findings, stats) {
  const { byRel, byStem } = index;
  POINTER_RE.lastIndex = 0;
  let m;
  while ((m = POINTER_RE.exec(line))) {
    const num = m[1];
    const at = m.index;
    const afterNum = at + 1 + num.length;
    const sp = line[afterNum];
    if (sp !== " " && sp !== "\u3000") {
      stats.skipped += 1;
      if (emitLog && VERBOSE) {
        console.log(`  skip form  ${sourceRel}:${lineNo}  §${num} (no space+title)`);
      }
      continue;
    }
    let nameStart = afterNum + 1;
    while (
      nameStart < line.length &&
      (line[nameStart] === " " || line[nameStart] === "\u3000")
    ) {
      nameStart += 1;
    }
    const rest = line.slice(nameStart);
    if (!rest) {
      stats.skipped += 1;
      continue;
    }

    const target =
      linkTargetAt(line, at, fromAbs, byRel) ||
      lookbehindTarget(line, at, fromAbs, byRel, byStem) ||
      tableRowTarget(line, fromAbs, byRel) ||
      intraDocTarget(sourceRel, rest, byRel);

    if (!target) {
      stats.skipped += 1;
      if (emitLog && VERBOSE) {
        console.log(`  skip file  ${sourceRel}:${lineNo}  §${num} ${rest.slice(0, 24)}`);
      }
      continue;
    }

    const doc = byRel.get(target);
    if (!doc) {
      stats.skipped += 1;
      continue;
    }
    const matched = matchHeading(rest, doc.headings);
    if (!matched) {
      stats.skipped += 1;
      if (emitLog && VERBOSE) {
        console.log(
          `  skip name  ${sourceRel}:${lineNo}  §${num} (title not a chapter of ${target})`,
        );
      }
      continue;
    }

    stats.checked += 1;
    if (emitLog && LIST) {
      console.log(`  check  ${sourceRel}:${lineNo}  §${num} ${matched.title}  → ${target}`);
    }
    if (matched.num === num) continue;

    const expectedH = doc.headings.find((h) => h.num === num);
    findings.push({
      file: sourceRel,
      line: lineNo,
      pointer: `§${num} ${matched.title}`,
      target,
      expected: expectedH ? headingLine(expectedH) : `no ## ${num}、`,
      actual: headingLine(matched),
    });
  }
}

function collectSourceFiles() {
  const files = [];
  for (const name of ["docs", ".cursor", "apps", "packages", "evals", "deploy"]) {
    walkFiles(join(ROOT, name), files);
  }
  try {
    for (const ent of readdirSync(ROOT, { withFileTypes: true })) {
      if (ent.isFile() && ent.name.endsWith(".md")) files.push(join(ROOT, ent.name));
    }
  } catch {
    /* ignore */
  }
  return files;
}

function runCheck(index) {
  /** @type {Finding[]} */
  const findings = [];
  const stats = { checked: 0, skipped: 0 };
  for (const abs of collectSourceFiles()) {
    const sourceRel = toPosixRel(abs);
    let content;
    try {
      content = readFileSync(abs, "utf8");
    } catch {
      continue;
    }
    const fenceable = sourceRel.endsWith(".md") || sourceRel.endsWith(".mdc");
    let inFence = false;
    let lineNo = 0;
    for (const raw of content.split(/\r?\n/)) {
      lineNo += 1;
      if (fenceable) {
        const t = raw.trimStart();
        if (t.startsWith("```") || t.startsWith("~~~")) {
          inFence = !inFence;
          continue;
        }
        if (inFence) continue;
      }
      if (!raw.includes("§")) continue;
      checkLine(raw, sourceRel, abs, index, lineNo, findings, stats);
    }
  }
  return { findings, stats };
}

function formatFinding(f) {
  return [
    `${f.file}:${f.line}`,
    `  pointer:  ${f.pointer}`,
    `  target:   ${f.target}`,
    `  expected: ${f.expected}`,
    `  actual:   ${f.actual}`,
  ].join("\n");
}

/** In-memory fixtures so a no-op matcher cannot ship green. */
function selfCheck() {
  const fakeRel = "docs/04-前端/前端技术与架构.md";
  const headings = [
    { num: "一", title: "技术栈" },
    { num: "十", title: "SSE 与协议一致性" },
    { num: "十一", title: "待定" },
  ];
  const byRel = new Map([
    [fakeRel, { rel: fakeRel, stem: "前端技术与架构", headings }],
    [
      "docs/03-AI核心/执行引擎架构设计.md",
      {
        rel: "docs/03-AI核心/执行引擎架构设计.md",
        stem: "执行引擎架构设计",
        headings: [{ num: "三", title: "执行管线" }],
      },
    ],
  ]);
  const byStem = new Map([
    ["前端技术与架构", fakeRel],
    ["执行引擎架构设计", "docs/03-AI核心/执行引擎架构设计.md"],
  ]);
  const index = { byRel, byStem };
  const fromAbs = join(ROOT, fakeRel.replace(/\//g, sep));

  /** @param {string} line */
  function one(line) {
    const findings = [];
    const stats = { checked: 0, skipped: 0 };
    checkLine(line, "apps/x.ts", fromAbs, index, 1, findings, stats);
    return { findings, stats };
  }

  const pass = one("golden (前端技术与架构 §十 SSE 与协议一致性).");
  if (pass.stats.checked !== 1 || pass.findings.length !== 0) {
    throw new Error(`self-check pass failed: ${JSON.stringify(pass)}`);
  }

  const eleven = one("code 前端技术与架构 §十一 待定");
  if (eleven.stats.checked !== 1 || eleven.findings.length !== 0) {
    throw new Error(`self-check 十一 failed: ${JSON.stringify(eleven)}`);
  }

  const badNum = one("stale 前端技术与架构 §十二 SSE 与协议一致性");
  if (badNum.findings.length !== 1 || badNum.stats.checked !== 1) {
    throw new Error(`self-check bad number failed: ${JSON.stringify(badNum)}`);
  }

  const badName = one("swap 前端技术与架构 §十 技术栈");
  if (badName.findings.length !== 1) {
    throw new Error(`self-check bad name failed: ${JSON.stringify(badName)}`);
  }

  const noName = one("old form 前端技术与架构.md §十二 later");
  if (noName.stats.checked !== 0 || noName.findings.length !== 0) {
    throw new Error(`self-check skip §十二-only failed: ${JSON.stringify(noName)}`);
  }

  const arabic = one("decision 发布与门禁 §9.12 leftover 前端技术与架构 §十 SSE 与协议一致性");
  if (arabic.stats.checked !== 1 || arabic.findings.length !== 0) {
    throw new Error(`self-check arabic skip failed: ${JSON.stringify(arabic)}`);
  }

  const midDot = one("前端技术与架构 §一·delegate ignored");
  if (midDot.stats.checked !== 0 || midDot.findings.length !== 0) {
    throw new Error(`self-check mid-dot skip failed: ${JSON.stringify(midDot)}`);
  }

  const nick = one("执行引擎架构设计 §三 长对话压缩");
  if (nick.stats.checked !== 0 || nick.findings.length !== 0) {
    throw new Error(`self-check nickname skip failed: ${JSON.stringify(nick)}`);
  }

  const table = [];
  const tableStats = { checked: 0, skipped: 0 };
  checkLine(
    "| x | [前端技术与架构](/docs/04-前端/前端技术与架构.md) | §一 技术栈 / §十 SSE 与协议一致性；另见 §五 客户端架构 |",
    "docs/04-前端/前端地图.md",
    join(ROOT, "docs", "04-前端", "前端地图.md"),
    index,
    21,
    table,
    tableStats,
  );
  if (tableStats.checked !== 2 || table.length !== 0) {
    throw new Error(
      `self-check table failed: checked=${tableStats.checked} findings=${table.length}`,
    );
  }

  const intra = [];
  const intraStats = { checked: 0, skipped: 0 };
  checkLine(
    "共享常量（§十 SSE 与协议一致性、fold-kit）。",
    fakeRel,
    fromAbs,
    index,
    45,
    intra,
    intraStats,
  );
  if (intraStats.checked !== 1 || intra.length !== 0) {
    throw new Error(`self-check intra-doc failed: ${JSON.stringify({ intra, intraStats })}`);
  }
}

function main() {
  try {
    selfCheck();
  } catch (err) {
    console.error("✗ doc section pointers — checker self-check failed");
    console.error(err instanceof Error ? err.message : err);
    process.exit(2);
  }

  emitLog = VERBOSE || LIST;
  const index = buildDocsIndex();
  const { findings, stats } = runCheck(index);

  if (findings.length) {
    console.error(`✗ doc section pointers — ${findings.length} stale`);
    console.error("");
    for (const f of findings) console.error(formatFinding(f));
    console.error("");
    console.error(
      `  checked ${stats.checked} compound pointer(s); skipped ${stats.skipped} (unresolved / non-chapter form).`,
    );
    process.exit(1);
  }

  console.log(
    `✓ doc section pointers — ${stats.checked} compound pointer(s) ok, ${stats.skipped} skipped`,
  );
}

main();
