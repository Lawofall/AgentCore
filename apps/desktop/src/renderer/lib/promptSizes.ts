import type { PromptCatalogItem, PromptRail } from "@/lib/promptCatalog";
import { skillBodyFromContent } from "@/services/skillCatalog";

/** Factory constitution slice — catalog id `shared`. */
export const BASE_CORE_LABEL = "基座+CEO核";
/** Role `<身份>` switcher — catalog id `identity`. */
export const IDENTITY_LABEL = "角色身份";

export interface AlwaysListRow {
  catalogId: string;
  label: string;
  meta: string;
  chars: number;
  item: PromptCatalogItem;
}

/** Raw injected length. Whitespace counts; we never convert this to a percentage. */
export function promptCharLen(text: string): number {
  return text.length;
}

/** Always-pool body length (frontmatter stripped), same rule as the catalog editor. */
export function promptBodyChars(content: string): number {
  return promptCharLen(skillBodyFromContent(content));
}

export function formatPromptChars(chars: number): string {
  return `${chars} 字`;
}

/** Tile subtitle: same floor as the files-page always meter — under 1k is omitted. */
export function formatAlwaysRowChars(chars: number): string | null {
  if (chars < 1000) return null;
  return formatPromptChars(chars);
}

function identityChars(
  item: Extract<PromptCatalogItem, { kind: "identity" }>,
): number {
  return Math.max(
    promptCharLen(item.ceoIdentity),
    promptCharLen(item.nestedIdentity),
    promptCharLen(item.leafIdentity),
  );
}

/** Prefer the always-pool count when the rail already measured it. */
function mineResidentChars(
  item: Extract<PromptCatalogItem, { kind: "mine" }>,
): number {
  if (typeof item.alwaysChars === "number") return item.alwaysChars;
  return promptBodyChars(item.content);
}

function alwaysItemChars(item: PromptCatalogItem): number {
  if (item.kind === "shared") return promptCharLen(item.text);
  if (item.kind === "identity") return identityChars(item);
  if (item.kind === "mine") return mineResidentChars(item);
  return 0;
}

function alwaysItemMeta(item: PromptCatalogItem): string {
  if (item.kind === "shared") return "每回合都在的工作宪法";
  if (item.kind === "identity") {
    const snippet = item.ceoIdentity.trim();
    if (snippet) return snippet;
    return "三套互斥身份，点开看全文";
  }
  if (item.kind === "mine") {
    if (item.disputed) return "已停用，不再注入";
    const description = item.description.trim();
    if (description && description !== item.label) return description;
    if (item.memoryKind === "preferences") return "怎么回答";
    if (item.memoryKind === "profile") return "关于用户";
    return "";
  }
  return "";
}

/** Always-pool index: includes empty cores so they stay openable. */
export function buildAlwaysRows(rail: PromptRail): AlwaysListRow[] {
  const rows: AlwaysListRow[] = [];
  for (const item of rail.constitution) {
    rows.push({
      catalogId: item.id,
      label: item.label,
      meta: alwaysItemMeta(item),
      chars: alwaysItemChars(item),
      item,
    });
  }
  for (const item of rail.memory) {
    if (item.kind !== "mine") continue;
    rows.push({
      catalogId: item.id,
      label: item.label,
      meta: alwaysItemMeta(item),
      chars: alwaysItemChars(item),
      item,
    });
  }
  for (const item of rail.alwaysMine) {
    if (item.kind !== "mine") continue;
    rows.push({
      catalogId: item.id,
      label: item.label,
      meta: alwaysItemMeta(item),
      chars: alwaysItemChars(item),
      item,
    });
  }
  return rows;
}
