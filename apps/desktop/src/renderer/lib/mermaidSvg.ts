/**
 * Mermaid v11 `useMaxWidth` (default) emits width="100%" + inline
 * `max-width: <native>px`. Strip the cap and pin pixel width/height so the
 * lightbox can contain-fit native px. Inline preview sizes a pixel box from
 * those native dimensions (see inlineMermaidBox) — do not stretch to column
 * width and do not put max-height + width/height:auto on the SVG (Chromium 0×0).
 */

function parsePxLen(raw: string | null): number {
  if (!raw) return 0;
  const t = raw.trim();
  if (!t || t.endsWith("%")) return 0;
  const n = Number.parseFloat(t);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function styleMaxWidthPx(style: string): number {
  const m = /max-width:\s*([\d.]+)px/i.exec(style);
  if (!m) return 0;
  const n = Number.parseFloat(m[1]);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function parseViewBoxSize(vb: string | null): { w: number; h: number } | null {
  if (!vb) return null;
  const p = vb
    .trim()
    .split(/[\s,]+/)
    .map(Number);
  if (p.length !== 4 || !(p[2] > 0) || !(p[3] > 0)) return null;
  return { w: p[2], h: p[3] };
}

function stripMaxWidth(style: string): string {
  return style
    .replace(/max-width:\s*[^;]+;?/gi, "")
    .replace(/;;+/g, ";")
    .replace(/^\s*;\s*|\s*;\s*$/g, "")
    .trim();
}

export function normalizeMermaidSvg(svg: string): string {
  if (!svg) return svg;
  try {
    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    if (doc.querySelector("parsererror")) return svg;
    const el = doc.documentElement;
    if (!el || el.tagName.toLowerCase() !== "svg") return svg;

    const style = el.getAttribute("style") ?? "";
    const vb = parseViewBoxSize(el.getAttribute("viewBox"));
    const w =
      styleMaxWidthPx(style) ||
      parsePxLen(el.getAttribute("width")) ||
      vb?.w ||
      0;
    const h = parsePxLen(el.getAttribute("height")) || vb?.h || 0;

    if (w && h && !el.getAttribute("viewBox")) {
      el.setAttribute("viewBox", `0 0 ${w} ${h}`);
    }
    if (w) el.setAttribute("width", String(w));
    if (h) el.setAttribute("height", String(h));

    const cleaned = stripMaxWidth(style);
    if (cleaned) el.setAttribute("style", cleaned);
    else el.removeAttribute("style");

    return new XMLSerializer().serializeToString(el);
  } catch {
    return svg;
  }
}

/** Native CSS-pixel size after {@link normalizeMermaidSvg}, else `{w:0,h:0}`. */
export function readMermaidSvgSize(svg: string): { w: number; h: number } {
  if (!svg) return { w: 0, h: 0 };
  try {
    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    const el = doc.documentElement;
    if (!el || el.tagName.toLowerCase() !== "svg") return { w: 0, h: 0 };
    const vb = parseViewBoxSize(el.getAttribute("viewBox"));
    const style = el.getAttribute("style") ?? "";
    const w =
      parsePxLen(el.getAttribute("width")) ||
      styleMaxWidthPx(style) ||
      vb?.w ||
      0;
    const h = parsePxLen(el.getAttribute("height")) || vb?.h || 0;
    return { w, h };
  } catch {
    return { w: 0, h: 0 };
  }
}

/** Native CSS-pixel width after {@link normalizeMermaidSvg}, else 0. */
export function readMermaidSvgWidth(svg: string): number {
  return readMermaidSvgSize(svg).w;
}
