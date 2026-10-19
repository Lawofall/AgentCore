import type { PromptCatalogItem } from "@/lib/promptCatalog";

/** Next omit list after flipping one official HOW. `on` means the row stays. */
export function omittedFactoryAfterToggle(
  omitted: readonly string[],
  id: string,
  on: boolean,
): string[] {
  const next = new Set(omitted);
  if (on) next.delete(id);
  else next.add(id);
  return [...next];
}

export function officialSkillNames(
  items: readonly PromptCatalogItem[],
): string[] {
  return items.flatMap((item) =>
    item.kind === "skill" ? [item.skill.name] : [],
  );
}
