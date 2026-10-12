import { uiGet, uiSet } from "@/lib/uiStorage";
import { create } from "zustand";

const THEME_KEY = "theme";
const DEVELOPER_MODE_KEY = "developer-mode";

type Theme = "light" | "dark" | "system";

// Theme is persisted so the choice survives a reload; it is *applied* to the DOM
// by lib/theme.ts (the store only holds the value). Falls back to 跟随系统.
function loadTheme(): Theme {
  const v = uiGet<string>(THEME_KEY);
  return v === "light" || v === "dark" || v === "system" ? v : "system";
}

function persistTheme(v: Theme): void {
  uiSet(THEME_KEY, v);
}

function loadDeveloperMode(): boolean {
  return uiGet<boolean>(DEVELOPER_MODE_KEY) === true;
}

function persistDeveloperMode(on: boolean): void {
  uiSet(DEVELOPER_MODE_KEY, on);
}

interface UIState {
  searchOpen: boolean;
  /** Prefill for the next palette open; consumed on open. */
  searchInitialQuery: string;
  theme: Theme;
  /** Desktop developer mode. Off until the user turns it on in 设置 · 通用. */
  developerMode: boolean;

  openSearch: (initialQuery?: string) => void;
  closeSearch: () => void;
  toggleSearch: () => void;
  setTheme: (theme: UIState["theme"]) => void;
  setDeveloperMode: (on: boolean) => void;
}

/** Full-screen turn detail view (`#/conversations/:id/turn/:turnId?view=`). */
export type TurnDetailView = "graph" | "debate";

/** Build the hash-route path for a turn's full-screen detail page. */
export function turnDetailPath(
  conversationId: string,
  turnId: string,
  view?: TurnDetailView,
): string {
  const path = `/conversations/${conversationId}/turn/${turnId}`;
  const params = new URLSearchParams();
  if (view) params.set("view", view);
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export const useUIStore = create<UIState>((set) => ({
  searchOpen: false,
  searchInitialQuery: "",
  theme: loadTheme(),
  developerMode: loadDeveloperMode(),

  // Default "" is required: Sidebar SearchTrigger calls openSearch() with no args.
  // Without it, searchInitialQuery becomes undefined and CommandPalette crashes
  // on query.trim() (regressed in 1ee81cee when the default was dropped).
  openSearch: (initialQuery) =>
    set({
      searchOpen: true,
      searchInitialQuery: initialQuery ?? "",
    }),
  closeSearch: () =>
    set({
      searchOpen: false,
      searchInitialQuery: "",
    }),
  toggleSearch: () => set((s) => ({ searchOpen: !s.searchOpen })),
  setTheme: (theme) => {
    persistTheme(theme);
    set({ theme });
  },
  setDeveloperMode: (on) => {
    persistDeveloperMode(on);
    set({ developerMode: on });
  },
}));
