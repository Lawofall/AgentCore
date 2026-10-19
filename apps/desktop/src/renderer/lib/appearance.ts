export const FONT_SIZES = ["sm", "md", "lg", "xl"] as const;
export type FontSize = (typeof FONT_SIZES)[number];

/** Multipliers on the 4-level type tokens (`xs`/`sm`/`base`/`xl`). Spacing stays. */
export const FONT_SIZE_SCALE: Record<FontSize, number> = {
  sm: 0.875,
  md: 1,
  lg: 1.125,
  xl: 1.25,
};

export const FONT_SIZE_LABEL: Record<FontSize, string> = {
  sm: "小",
  md: "默认",
  lg: "大",
  xl: "特大",
};

export const FONT_SIZE_ITEMS = FONT_SIZES.map((value) => ({
  value,
  label: FONT_SIZE_LABEL[value],
}));

export const ZOOM_DEFAULT = 1;
export const ZOOM_MIN = 0.8;
export const ZOOM_MAX = 1.5;
export const ZOOM_STEP = 0.1;

export function isFontSize(v: unknown): v is FontSize {
  return v === "sm" || v === "md" || v === "lg" || v === "xl";
}

export function clampZoom(n: number): number {
  if (!Number.isFinite(n)) return ZOOM_DEFAULT;
  const stepped = Math.round(n / ZOOM_STEP) * ZOOM_STEP;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number(stepped.toFixed(1))));
}

export function zoomStep(current: number, direction: 1 | -1): number {
  return clampZoom(current + direction * ZOOM_STEP);
}

export function formatZoomPercent(zoom: number): string {
  return `${Math.round(clampZoom(zoom) * 100)}%`;
}

/** Scale the 4-level type tokens. Safe before React mounts (see main.tsx). */
export function applyFontSize(size: FontSize): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty(
    "--font-scale",
    String(FONT_SIZE_SCALE[size]),
  );
}

/**
 * Whole-chrome zoom (Discord Zoom / browser Ctrl±). Chromium `zoom` on `<html>`
 * so Electron, production web, and Capacitor share one path. Spacing and icons
 * grow with this; reading type still has its own `--font-scale`.
 */
export function applyZoom(factor: number): void {
  if (typeof document === "undefined") return;
  const next = clampZoom(factor);
  const html = document.documentElement;
  if (next === ZOOM_DEFAULT) {
    html.style.removeProperty("zoom");
    return;
  }
  html.style.setProperty("zoom", String(next));
}
