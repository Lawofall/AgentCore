import type { PromptCatalogItem, PromptRail } from "@/lib/promptCatalog";
import { skillBodyFromContent } from "@/services/skillCatalog";

/** Factory constitution slice — catalog id `shared`. */
export const BASE_CORE_LABEL = "基座+CEO核";

export interface AlwaysListRow {
  catalogId: string;
  label: string;
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

/** 必带 tile subtitle. That zone is already all always-on, so under 1k is omitted. */
export function formatAlwaysRowChars(chars: number): string | null {
  if (chars < 1000) return null;
  return formatPromptChars(chars);
}

function mineResidentChars(
  item: Extract<PromptCatalogItem, { kind: "mine" }>,
): number {
  if (typeof item.alwaysChars === "number") return item.alwaysChars;
  return promptBodyChars(item.content);
}

function alwaysItemChars(item: PromptCatalogItem): number {
  if (item.kind === "shared") return promptCharLen(item.text);
  if (item.kind === "mine") return mineResidentChars(item);
  return 0;
}

/** Always-pool index: constitution + user-written always rows. */
export function buildAlwaysRows(rail: PromptRail): AlwaysListRow[] {
  const rows: AlwaysListRow[] = [];
  for (const item of rail.constitution) {
    rows.push({
      catalogId: item.id,
      label: item.label,
      chars: alwaysItemChars(item),
      item,
    });
  }
  for (const item of rail.alwaysMine) {
    if (item.kind !== "mine") continue;
    rows.push({
      catalogId: item.id,
      label: item.label,
      chars: alwaysItemChars(item),
      item,
    });
  }
  return rows;
}
