/**
 * Viewport policy for narrow / Capacitor chrome — not physical capabilities.
 * 权威 → 前端技术 §五：窄屏不上工具箱 / 白板 / 手册 / 站立任务 / 快捷键设置…
 */

export const NARROW_HIDDEN_SETTINGS_PATHS = new Set([
  "/more/git",
  "/more/general",
  "/more/shortcuts",
  "/more/feedback",
  "/more/appearance",
]);

const NARROW_BLOCKED_PREFIXES = [
  "/whiteboard",
  "/toolbox",
  "/explore",
] as const;

export const NARROW_HIDDEN_PALETTE_IDS = new Set([
  "toggle-sidebar",
  "open-workspace-terminal",
  "new-folder",
  "connect-git",
  "import-to-cloud",
  "borrow-to-cloud",
  "open-local-project",
  "grant-readonly-folder",
  "nav-conversations",
  "nav-whiteboard",
  "nav-toolbox",
  "nav-tools",
  "nav-guidelines",
  "nav-manual",
  "nav-mechanism",
  "nav-automations",
  "nav-workflows",
  "nav-automations-inbox",
  "nav-settings-general",
  "nav-settings-shortcuts",
  "nav-preview",
]);

const NARROW_HIDDEN_THEME_IDS = new Set(["theme-dark", "theme-system"]);

export function isNarrowBlockedPath(pathname: string): boolean {
  if (pathname === "/conversations") return true;
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
