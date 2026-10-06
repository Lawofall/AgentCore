import type { LlmModelProfileView } from "@/services/llmModelProfiles";

/**
 * A materialized preset stays on its preset name while the recipe is still
 * locked and the name is still the recipe's name. Renaming, blanking, or
 * editing tools / the envelope / the factory catalog / plugs gives it its
 * own chip. A dismissed preset leaves the owned row visible.
 */
export function foldedIntoPreset(
  row: LlmModelProfileView,
  presets: readonly LlmModelProfileView[],
): LlmModelProfileView | undefined {
  if (row.kind !== "user" || !row.recipe) return undefined;
  const preset = presets.find(
    (item) => item.kind === "system" && item.recipe === row.recipe,
  );
  if (!preset || preset.name !== row.name) return undefined;
  return preset;
}
