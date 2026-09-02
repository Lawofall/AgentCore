/**
 * Denser-than-default flowchart layout for chat figures. Mermaid's stock
 * nodeSpacing/rankSpacing (50) plus 16px labels make a TD stack taller than a
 * reading column; the inline card contain-fits into an explicit pixel box
 * (Diagram.tsx). wrappingWidth stays at mermaid's 200 so CJK node text still
 * wraps instead of inflating node boxes.
 *
 * useMaxWidth must stay true: false emits a bare pixel width with no CSS
 * max-width, and stretching that SVG to the column enlarges compact charts
 * (239×677 → 616×1744 in Chromium).
 *
 * Palette comes from design tokens (not mermaid's stock default/dark). khroma
 * cannot parse oklch(), so values are converted to a canvas-resolved rgb/hex
 * when the DOM is available. Fallbacks match tokens.css :root / .dark.
 *
 * Do not `import "mermaid"` from this module — the renderer lazy-loads it
 * (Vite deps race; see Diagram.tsx).
 */
export const MERMAID_FLOWCHART_LAYOUT = {
  nodeSpacing: 32,
  rankSpacing: 36,
  diagramPadding: 8,
  padding: 8,
  wrappingWidth: 200,
  useMaxWidth: true,
} as const;

/** Body-adjacent label size (markdown-body --text-sm ≈ 0.875rem), not mermaid's 16. */
export const MERMAID_FONT_SIZE_PX = 14;

type TokenPaint = {
  background: string;
  foreground: string;
  card: string;
  muted: string;
  mutedForeground: string;
  border: string;
  destructive: string;
};

/** Keep in sync with packages/design-tokens/src/tokens.css (:root / .dark). */
const TOKEN_PAINT: { light: TokenPaint; dark: TokenPaint } = {
  light: {
    background: "oklch(1 0 0)",
    foreground: "oklch(0.15 0.01 255)",
    card: "oklch(1 0 0)",
    muted: "oklch(0.97 0.006 255)",
    mutedForeground: "oklch(0.55 0.02 255)",
    border: "oklch(0.92 0.008 255)",
    destructive: "oklch(0.58 0.22 27)",
  },
  dark: {
    background: "oklch(0.13 0.004 255)",
    foreground: "oklch(0.93 0.005 255)",
    card: "oklch(0.185 0.004 255)",
    muted: "oklch(0.225 0.005 255)",
    mutedForeground: "oklch(0.72 0.01 255)",
    border: "oklch(1 0 0 / 0.12)",
    destructive: "oklch(0.65 0.19 27)",
  },
};

function readToken(name: string, fallback: string): string {
  if (
    typeof document === "undefined" ||
    typeof getComputedStyle !== "function"
  ) {
    return fallback;
  }
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw || raw.includes("var(")) return fallback;
  return raw;
}

/** mermaid/khroma cannot parse oklch(); canvas fillStyle can. */
function toMermaidColor(color: string): string {
  if (typeof document === "undefined") return color;
  if (typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent)) {
    return color;
  }
  try {
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) return color;
    ctx.fillStyle = "#000";
    ctx.fillStyle = color;
    return ctx.fillStyle || color;
  } catch {
    return color;
  }
}

function paint(dark: boolean): TokenPaint {
  const fb = dark ? TOKEN_PAINT.dark : TOKEN_PAINT.light;
  return {
    background: toMermaidColor(readToken("--background", fb.background)),
    foreground: toMermaidColor(readToken("--foreground", fb.foreground)),
    card: toMermaidColor(readToken("--card", fb.card)),
    muted: toMermaidColor(readToken("--muted", fb.muted)),
    mutedForeground: toMermaidColor(
      readToken("--muted-foreground", fb.mutedForeground),
    ),
    border: toMermaidColor(readToken("--border", fb.border)),
    destructive: toMermaidColor(readToken("--destructive", fb.destructive)),
  };
}

export function mermaidThemeVariables(dark: boolean) {
  const t = paint(dark);
  return {
    darkMode: dark,
    background: t.background,
    fontSize: `${MERMAID_FONT_SIZE_PX}px`,
    primaryColor: t.card,
    primaryTextColor: t.foreground,
    primaryBorderColor: t.border,
    secondaryColor: t.muted,
    secondaryTextColor: t.foreground,
    secondaryBorderColor: t.border,
    tertiaryColor: t.background,
    tertiaryTextColor: t.mutedForeground,
    tertiaryBorderColor: t.border,
    lineColor: t.mutedForeground,
    textColor: t.foreground,
    mainBkg: t.card,
    nodeBorder: t.border,
    clusterBkg: t.muted,
    clusterBorder: t.border,
    titleColor: t.foreground,
    edgeLabelBackground: t.background,
    noteBkgColor: t.muted,
    noteTextColor: t.foreground,
    noteBorderColor: t.border,
    errorBkgColor: t.destructive,
    errorTextColor: t.foreground,
    altSectionBkgColor: t.background,
    useGradient: false,
    dropShadow: "none",
  };
}

export function mermaidRenderConfig(dark: boolean) {
  return {
    startOnLoad: false,
    securityLevel: "strict" as const,
    theme: "base" as const,
    fontFamily: "inherit",
    fontSize: MERMAID_FONT_SIZE_PX,
    themeVariables: mermaidThemeVariables(dark),
    flowchart: { ...MERMAID_FLOWCHART_LAYOUT },
  };
}
