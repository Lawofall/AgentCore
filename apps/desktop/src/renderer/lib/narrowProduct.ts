/**
 * Viewport policy for narrow / Capacitor chrome — not physical capabilities.
 * 权威 → 前端技术 §五：窄屏不上工具箱 / 手册 / 快捷键设置…
 * 通用露出字号与发送方式（页内藏主题 / 缩放 / 系统通知 / 开发者模式）。
 * 联网搜索是独立设置页，窄屏仍在。
 */

export const NARROW_HIDDEN_SETTINGS_PATHS = new Set([
  "/more/git",
  "/more/shortcuts",
  "/more/appearance",
]);

const NARROW_BLOCKED_PREFIXES = ["/toolbox", "/explore"] as const;

export const NARROW_HIDDEN_PALETTE_IDS = new Set([
  "toggle-sidebar",
  "open-workspace-terminal",
  "connect-git",
  "borrow-to-cloud",
  "open-local-project",
  "grant-readonly-folder",
  "nav-toolbox",
  "nav-guidelines",
  "nav-official",
  "nav-mcp",
  "nav-store",
  "nav-manual",
  "nav-mechanism",
  "nav-settings-shortcuts",
  "nav-preview",
  "zoom-in",
  "zoom-out",
  "zoom-reset",
]);

const NARROW_HIDDEN_THEME_IDS = new Set(["theme-dark", "theme-system"]);

export function isNarrowBlockedPath(pathname: string): boolean {
  if (NARROW_HIDDEN_SETTINGS_PATHS.has(pathname)) return true;
  return NARROW_BLOCKED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function isNarrowHiddenPaletteId(
  id: string,
  opts: { restrictNarrow: boolean; forceLightTheme: boolean },
): boolean {
  if (opts.restrictNarrow && NARROW_HIDDEN_PALETTE_IDS.has(id)) return true;
  if (opts.forceLightTheme && NARROW_HIDDEN_THEME_IDS.has(id)) return true;
  return false;
}

export function narrowBlockedRedirect(pathname: string): string {
  if (NARROW_HIDDEN_SETTINGS_PATHS.has(pathname)) return "/more";
  return "/";
}
