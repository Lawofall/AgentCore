import { formatCompact } from "@/lib/format";
import {
  type ModelCatalogItem,
  type ModelOrigin,
  findCatalogItem,
} from "@/services/models";

/** Compaction near-ceiling default; ring tint only, not a second product. */
export const WINDOW_FILL_WARN_RATIO = 0.8;

export type WindowFill = {
  used: number;
  window: number;
  ratio: number;
  percent: number;
};

export type LastPromptUsage = { last_prompt?: number };

/** Minimal frame slice — `run_completed` carries role / model / usage. */
export type CaptainPromptFrame = {
  kind: string;
  role?: string;
  model?: string;
  usage?: LastPromptUsage;
};

export function positiveTokens(n: unknown): number | null {
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? n : null;
}

export type SettledPromptMessage = {
  role: string;
  usage?: LastPromptUsage | null;
};

/**
 * Composer ring numerator.
 *
 * One session waterline: the latest CEO request that returned usage. A later
 * call replaces it, including a smaller prompt after compact or a larger one
 * after resume. When this session has not observed a call yet, use the newest
 * settled receipt so a reload (and the gap before the next turn's first
 * usage) still shows the previous request.
 */
export function sessionWindowPrompt(
  measured: number | null | undefined,
  messages: readonly SettledPromptMessage[],
): number | null {
  const live = positiveTokens(measured);
  if (live != null) return live;
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.role !== "assistant") continue;
    const settled = positiveTokens(message.usage?.last_prompt);
    if (settled != null) return settled;
  }
  return null;
}

export function captainCompletedModel(
  frames: readonly CaptainPromptFrame[],
): string | null {
  for (let i = frames.length - 1; i >= 0; i--) {
    const frame = frames[i];
    if (frame.kind !== "run_completed" || frame.role !== "captain") continue;
    const model = frame.model?.trim();
    return model || null;
  }
  return null;
}

export function catalogContextLength(
  models: ModelCatalogItem[],
  opts: {
    modelId?: string | null;
    slot?: {
      model: string;
      origin: ModelOrigin;
      provider_id?: string | null;
    } | null;
  },
): number | null {
  if (opts.modelId) {
    const hits = models.filter((m) => m.id === opts.modelId);
    const item = hits.find((m) => m.available) ?? hits[0];
    const n = positiveTokens(item?.context_length);
    if (n != null) return n;
  }
  const slot = opts.slot;
  if (slot?.model) {
    const item = findCatalogItem(models, {
      id: slot.model,
      origin: slot.origin,
      providerId: slot.provider_id,
    });
    return positiveTokens(item?.context_length);
  }
  return null;
}

export function windowFill(used: number, window: number): WindowFill | null {
  if (!Number.isFinite(used) || !Number.isFinite(window)) return null;
  if (used <= 0 || window <= 0) return null;
  const ratio = used / window;
  return {
    used,
    window,
    ratio,
    percent: Math.round(ratio * 100),
  };
}

export function windowFillLabel(
  fill: WindowFill | null,
  used: number | null,
): string {
  if (fill) {
    return `${formatCompact(fill.used)} / ${formatCompact(fill.window)}`;
  }
  if (used != null) return formatCompact(used);
  return "收到的上下文";
}
