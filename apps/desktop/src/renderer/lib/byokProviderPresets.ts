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

/**
 * Console origin → OpenAI-compat API root.
 * Empty path becomes `{origin}/v1`. Preset official endpoints and URLs that
 * already have a path are left as-is. Trailing slashes are stripped.
 */
export function canonicalizeByokApiRoot(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return trimmed;
  let stripped = trimmed;
  while (stripped.endsWith("/")) {
    stripped = stripped.slice(0, -1);
  }
  if (!stripped) return trimmed;
  if (resolveByokProviderFromConfig(stripped) !== BYOK_CUSTOM_PROVIDER_ID) {
    return stripped;
  }
  try {
    const parsed = new URL(stripped);
    const path = parsed.pathname.replace(/\/+$/, "");
    if (path === "" || path === "/") {
      return `${stripped}/v1`;
    }
  } catch {
    return stripped;
  }
  return stripped;
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

/** Saved vendor identity. Unknown / missing → custom. Never inferred from URL. */
export function parseByokVendorId(
  raw: string | undefined | null,
): ByokProviderId {
  const vid = (raw ?? "").trim();
  if (vid === BYOK_CUSTOM_PROVIDER_ID) return vid;
  if (PRESET_BY_ID.has(vid as Exclude<ByokProviderId, "custom">)) {
    return vid as ByokProviderId;
  }
  return BYOK_CUSTOM_PROVIDER_ID;
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
