/**
 * Export toolbox manual sections → server product_help_corpus.json.
 *
 * Usage (from apps/desktop):
 *   pnpm exec tsx scripts/export-manual-corpus.ts
 *   pnpm exec tsx scripts/export-manual-corpus.ts --check
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { CONTENT_CHAPTERS } from "../src/renderer/pages/toolbox/manual/content/index.ts";
import { extractBlockText } from "../src/renderer/pages/toolbox/manual/searchIndex.ts";
import type { ManualChapterId } from "../src/renderer/pages/toolbox/manual/paths.ts";
import { MANUAL_SECTION_ALIASES, manualHref } from "../src/renderer/pages/toolbox/manual/sectionIds.ts";
import type {
  ManualAiPlacement,
  ManualBlock,
  ManualSurface,
} from "../src/renderer/pages/toolbox/manual/types.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const OUT = join(
  ROOT,
  "apps",
  "server",
  "agentcore",
  "runtime",
  "skills",
  "product_help_corpus.json",
);

const ALL_SURFACES: ManualSurface[] = ["desktop", "web", "mobile"];

interface CorpusSection {
  id: string;
  chapter: string;
  title: string;
  ai: ManualAiPlacement;
  availability: ManualSurface[];
  href: string;
  text: string;
}

interface CorpusFile {
  version: 1;
  sections: CorpusSection[];
  aliases: Record<string, string>;
}

function corpusBlockText(block: ManualBlock): string {
  if (block.type !== "doDont") return extractBlockText(block);
  const good = block.good.label ?? "这样说";
  const bad = block.bad.label ?? "别这样";
  return [good, ...block.good.items, bad, ...block.bad.items].join("\n");
}

function buildCorpus(): CorpusFile {
  const sections: CorpusSection[] = [];
  for (const chapter of CONTENT_CHAPTERS) {
    const chapterId = chapter.id as ManualChapterId;
    for (const section of chapter.sections) {
      if (section.ai === "off") continue;
      const text = section.blocks
        .map((b) => corpusBlockText(b).trim())
        .filter(Boolean)
        .join("\n\n");
      sections.push({
        id: section.id,
        chapter: chapter.id,
        title: section.title,
        ai: section.ai ?? "default",
        availability: section.availability ?? [...ALL_SURFACES],
        href: `#${manualHref(chapterId, section.id)}`,
        text,
      });
    }
  }
  const exported = new Set(sections.map((s) => s.id));
  const aliases: Record<string, string> = {};
  for (const [alias, target] of Object.entries(MANUAL_SECTION_ALIASES)) {
    if (exported.has(target)) aliases[alias] = target;
  }
  return {
    version: 1,
    sections,
    aliases,
  };
}

function serialize(corpus: CorpusFile): string {
  return `${JSON.stringify(corpus, null, 2)}\n`;
}

const checkOnly = process.argv.includes("--check");
const next = serialize(buildCorpus());
if (checkOnly) {
  let prev = "";
  try {
    prev = readFileSync(OUT, "utf8");
  } catch {
    console.error(`sync-manual-corpus — missing ${OUT}; run pnpm sync:manual-corpus`);
    process.exit(1);
  }
  if (prev !== next) {
    console.error(
      "sync-manual-corpus — product_help_corpus.json is stale. Run: pnpm sync:manual-corpus",
    );
    process.exit(1);
  }
  console.log("sync-manual-corpus — ok");
  process.exit(0);
}

writeFileSync(OUT, next, "utf8");
console.log(`sync-manual-corpus — wrote ${OUT}`);
