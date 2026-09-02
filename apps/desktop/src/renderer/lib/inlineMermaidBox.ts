/**
 * Inline mermaid preview box — contain into the reading column, never upscale.
 *
 * Stretching a compact SVG to column width (`width:100%` on a magazine wrapper)
 * is what made TD stacks larger (Chromium: 239×677 → 616×1744). CSS
 * `width:auto; height:auto; max-height` on the SVG collapses to 0 in Chromium.
 * The card therefore sets an explicit contain-fit width×height; the SVG fills
 * that box via viewBox.
 */

export const MERMAID_INLINE_MAX_HEIGHT_VH = 0.5;
/** Chat preview ceiling. 36rem ≈ 576px; on a 1200px-tall window 50vh is 600px
 * so rem still bites. Same rem as checkpoint / commence cards; vh stays 50%
 * so short windows do not grow with the rem bump. */
export const MERMAID_INLINE_MAX_HEIGHT_REM = 36;

export function mermaidInlineMaxHeightPx(
  viewportH: number,
  remPx: number,
): number {
  const vhCap =
    viewportH > 0
      ? viewportH * MERMAID_INLINE_MAX_HEIGHT_VH
      : Number.POSITIVE_INFINITY;
  const remCap =
    remPx > 0
      ? MERMAID_INLINE_MAX_HEIGHT_REM * remPx
      : Number.POSITIVE_INFINITY;
  const cap = Math.min(vhCap, remCap);
  return Number.isFinite(cap) && cap > 0 ? Math.round(cap) : 0;
}

export type MermaidInlineBox = { w: number; h: number };

/**
 * Largest size that fits in `columnW × maxH` without exceeding native px.
 * `null` when native size is unknown — caller should fall back to a scroll
 * preview, not a stretch-to-column.
 */
export function inlineMermaidBoxPx(
  nativeW: number,
  nativeH: number,
  columnW: number,
  maxH: number,
): MermaidInlineBox | null {
  if (nativeW <= 0 || nativeH <= 0) return null;
  let scale = 1;
  if (columnW > 0) scale = Math.min(scale, columnW / nativeW);
  if (maxH > 0) scale = Math.min(scale, maxH / nativeH);
  return {
    w: Math.max(1, Math.round(nativeW * scale)),
    h: Math.max(1, Math.round(nativeH * scale)),
  };
}
