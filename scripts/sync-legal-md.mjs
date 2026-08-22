#!/usr/bin/env node
/**
 * Mirror in-app legal copy → public repo root Markdown.
 *
 * Canonical SoT: apps/desktop/src/renderer/pages/legal/content.ts
 *   → TERMS.md / PRIVACY.md（仓根公开镜像）
 *
 * Usage:
 *   node scripts/sync-legal-md.mjs          # write md
 *   node scripts/sync-legal-md.mjs --check  # fail if md ≠ desktop
 *
 * Wired: package.json `sync:legal` / `sync:legal:check`；release:gate contracts 段。
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DESKTOP_CONTENT = join(
  ROOT,
  "apps",
  "desktop",
  "src",
  "renderer",
  "pages",
  "legal",
  "content.ts",
);
const TERMS_OUT = join(ROOT, "TERMS.md");
const PRIVACY_OUT = join(ROOT, "PRIVACY.md");

const checkOnly = process.argv.includes("--check");

const HEADER = `> **与应用内法律文案同源**（单一源：\`apps/desktop/src/renderer/pages/legal/content.ts\`）。  
> **正式上线前须法务审阅**——本文档为产品正文公开镜像，**不构成**已审结论。  
> 同步：\`pnpm sync:legal\` · 漂移检查：\`pnpm sync:legal:check\`

`;

function loadLegalDocs(filePath) {
  if (!existsSync(filePath)) {
    console.error(`sync-legal — missing: ${filePath}`);
    process.exit(1);
  }
  let src = readFileSync(filePath, "utf8");
  src = src.replace(/^import\s+[\s\S]*?;\s*/m, "");
  const cutList = src.search(/export\s+const\s+LEGAL_DOC_LIST/);
  const cutFn = src.search(/export\s+function\s+getLegalDoc/);
  const cuts = [cutList, cutFn].filter((i) => i >= 0);
  if (cuts.length) src = src.slice(0, Math.min(...cuts));
  src = src.replace(
    /export\s+const\s+LEGAL_DOCS\s*:\s*Record<[^>]+>/,
    "const LEGAL_DOCS",
  );
  src = src.replace(/\bexport\s+/g, "");
  try {
    // content.ts is plain object literals + string const; no TS syntax left after strip.
    return new Function(`${src}\nreturn LEGAL_DOCS;`)();
  } catch (e) {
    console.error(`sync-legal — failed to evaluate ${filePath}: ${e.message}`);
    process.exit(1);
  }
}

function renderDoc(doc) {
  const lines = [`# ${doc.title}`, "", `**更新日期**：${doc.updatedAt}`, ""];
  for (const section of doc.sections) {
    lines.push(`## ${section.heading}`, "");
    for (const p of section.paragraphs ?? []) {
      lines.push(p, "");
    }
    if (section.bullets?.length) {
      for (const b of section.bullets) {
        lines.push(`- ${b}`);
      }
      lines.push("");
    }
  }
  return HEADER + lines.join("\n").replace(/\n+$/, "\n");
}

function normalizeNewlines(text) {
  return text.replace(/\r\n/g, "\n");
}

function assertSame(label, path, expected) {
  if (!existsSync(path)) {
    console.error(`sync-legal — missing ${label}: ${path}`);
    process.exit(1);
  }
  const actual = normalizeNewlines(readFileSync(path, "utf8"));
  if (actual !== expected) {
    console.error(`sync-legal — drift: ${label}`);
    console.error(`  expected from: ${DESKTOP_CONTENT}`);
    console.error(`  actual file:   ${path}`);
    console.error("  Fix: pnpm sync:legal");
    process.exit(1);
  }
}

const desktopDocs = loadLegalDocs(DESKTOP_CONTENT);
if (!desktopDocs?.terms || !desktopDocs?.privacy) {
  console.error("sync-legal — desktop LEGAL_DOCS missing terms/privacy");
  process.exit(1);
}

const termsMd = renderDoc(desktopDocs.terms);
const privacyMd = renderDoc(desktopDocs.privacy);

if (checkOnly) {
  assertSame("TERMS.md", TERMS_OUT, termsMd);
  assertSame("PRIVACY.md", PRIVACY_OUT, privacyMd);
  console.log("sync-legal — check ok (desktop ↔ TERMS/PRIVACY)");
  process.exit(0);
}

writeFileSync(TERMS_OUT, termsMd, "utf8");
writeFileSync(PRIVACY_OUT, privacyMd, "utf8");
console.log("sync-legal — wrote TERMS.md + PRIVACY.md");
