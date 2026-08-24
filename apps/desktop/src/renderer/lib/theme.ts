import { useUIStore } from "@/stores/ui";
import { useEffect } from "react";

export type Theme = "light" | "dark" | "system";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(DARK_QUERY).matches
  );
}

/** Resolve a theme choice to a concrete light/dark decision (`system` defers to
 * the OS). The DOM dark variant is class-based (`.dark` on the root, see
 * globals.css `@custom-variant dark`), so everything funnels through this. */
export function resolveDark(theme: Theme): boolean {
  return theme === "dark" || (theme === "system" && systemPrefersDark());
}

/** Toggle the root `.dark` class to match the selected theme. Safe to call
 * before React mounts (used by main.tsx to avoid a first-paint flash) and is
 * idempotent, so the {@link useApplyTheme} effect can re-run it freely. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", resolveDark(theme));
  window.windowApi?.setThemeSource?.(theme);
}

/**
 * Keep the DOM theme in sync with the UI store. Mounted once at the app shell.
 *
 * Re-applies whenever the user's choice changes, and — only while the choice is
 * `system` — follows live OS appearance changes. This is the single consumer
 * that turns the persisted `theme` value into an actual `.dark` toggle (the
 * store and the command palette just set the value).
 */
export function useApplyTheme(forceLight = false): void {
  const theme = useUIStore((s) => s.theme);
  useEffect(() => {
    applyTheme(forceLight ? "light" : theme);
    if (forceLight || theme !== "system" || typeof window === "undefined") {
      return;
    }
    const mq = window.matchMedia(DARK_QUERY);
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme, forceLight]);
}
