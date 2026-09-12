/**
 * BYOK vendor presets — frontend catalog for model settings.
 *
 * Table source: `apps/server/agentcore/llm/byok_provider_presets.json`
 * (same file the server catalog seed loads). Users may still override model
 * names (their key determines what actually works).
 */

import raw from "@byok-presets";

export type ByokProviderId =
  | "openai"
  | "deepseek"
  | "moonshot"
  | "zhipu"
  | "doubao"
  | "hy"
  | "openrouter"
  | "opencode_zen"
  | "opencode_go"
  | "custom";

export interface ByokProviderPreset {
  id: Exclude<ByokProviderId, "custom">;
  label: string;
  baseUrl: string;
  /** Alternate base URLs that should map to this preset (e.g. regional endpoints). */
  baseUrlAliases?: readonly string[];
  defaultModel: string;
  models: readonly string[];
  /** Exact ids omitted from the chat picker after seed ∪ discovery. */
  hideFromPicker?: readonly string[];
  keyHelpUrl?: string;
}

export const BYOK_CUSTOM_PROVIDER_ID = "custom" as const;

export const BYOK_PROVIDER_PRESETS: readonly ByokProviderPreset[] =
  raw.presets as readonly ByokProviderPreset[];

export const DEFAULT_BYOK_PROVIDER_ID: Exclude<ByokProviderId, "custom"> =
  "deepseek";

const PRESET_BY_ID = new Map(
  BYOK_PROVIDER_PRESETS.map((preset) => [preset.id, preset]),
);

/** Normalize base_url for preset matching (case, trailing slashes). */
export function normalizeByokBaseUrl(url: string): string {
  let normalized = url.trim().toLowerCase();
  while (normalized.endsWith("/")) {
    normalized = normalized.slice(0, -1);
  }
  return normalized;
}

function presetBaseUrls(preset: ByokProviderPreset): string[] {
  return [preset.baseUrl, ...(preset.baseUrlAliases ?? [])];
}

export function getByokProviderPreset(
  id: Exclude<ByokProviderId, "custom">,
): ByokProviderPreset {
  const preset = PRESET_BY_ID.get(id);
  if (!preset) {
    throw new Error(`Unknown BYOK provider preset: ${id}`);
  }
  return preset;
}

export function isCustomByokProvider(
  id: ByokProviderId,
): id is typeof BYOK_CUSTOM_PROVIDER_ID {
  return id === BYOK_CUSTOM_PROVIDER_ID;
}

/** Match stored base_url to a preset, or fall back to custom.
 * Equality is after normalize only — never prefix-match, or
 * OpenCode `/zen/v1` and `/zen/go/v1` would collide.
 */
export function resolveByokProviderFromConfig(baseUrl: string): ByokProviderId {
  const trimmed = baseUrl.trim();
  if (!trimmed) return DEFAULT_BYOK_PROVIDER_ID;

  const normalized = normalizeByokBaseUrl(trimmed);
  for (const preset of BYOK_PROVIDER_PRESETS) {
    if (
      presetBaseUrls(preset).some(
        (candidate) => normalizeByokBaseUrl(candidate) === normalized,
      )
    ) {
      return preset.id;
    }
  }
  return BYOK_CUSTOM_PROVIDER_ID;
}

export function listByokProviderOptions(): Array<{
  id: ByokProviderId;
  label: string;
}> {
  return [
    ...BYOK_PROVIDER_PRESETS.map((preset) => ({
      id: preset.id,
      label: preset.label,
    })),
    { id: BYOK_CUSTOM_PROVIDER_ID, label: "自定义" },
  ];
}
