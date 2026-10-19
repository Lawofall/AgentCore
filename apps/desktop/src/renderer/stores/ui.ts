import {
  type FontSize,
  ZOOM_DEFAULT,
  clampZoom,
  isFontSize,
  zoomStep,
} from "@/lib/appearance";
import { uiGet, uiSet } from "@/lib/uiStorage";
import { create } from "zustand";

const THEME_KEY = "theme";
const DEVELOPER_MODE_KEY = "developer-mode";
const FONT_SIZE_KEY = "font-size";
const ZOOM_KEY = "zoom";
const SEND_ON_ENTER_KEY = "send-on-enter";
const OS_NOTIFICATIONS_KEY = "os-notifications";

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

function loadFontSize(): FontSize {
  const v = uiGet<string>(FONT_SIZE_KEY);
  return isFontSize(v) ? v : "md";
}

function persistFontSize(v: FontSize): void {
  uiSet(FONT_SIZE_KEY, v);
}

function loadZoom(): number {
  return clampZoom(uiGet<number>(ZOOM_KEY) ?? ZOOM_DEFAULT);
}

function persistZoom(v: number): void {
  uiSet(ZOOM_KEY, v);
}

function loadBool(key: string, fallback: boolean): boolean {
  const v = uiGet<boolean>(key);
  return typeof v === "boolean" ? v : fallback;
}

interface UIState {
  searchOpen: boolean;
  /** Prefill for the next palette open; consumed on open. */
  searchInitialQuery: string;
  theme: Theme;
  /** Desktop developer mode. Off until the user turns it on in 设置 · 通用. */
  developerMode: boolean;
  /** Reading type scale. Applied by `lib/appearance.ts`. */
  fontSize: FontSize;
  /** Whole-chrome zoom factor (1 = 100%). Applied by `lib/appearance.ts`. */
  zoom: number;
  /** Enter 发送（默认）。false = Enter 换行，Ctrl/Cmd+Enter 发送。 */
  sendOnEnter: boolean;
  /** Desktop OS banners when the window is not in front. Default on. */
  osNotifications: boolean;

  openSearch: (initialQuery?: string) => void;
  closeSearch: () => void;
  toggleSearch: () => void;
  setTheme: (theme: UIState["theme"]) => void;
  setDeveloperMode: (on: boolean) => void;
  setFontSize: (size: FontSize) => void;
  nudgeZoom: (direction: 1 | -1) => void;
  resetZoom: () => void;
  setSendOnEnter: (on: boolean) => void;
  setOsNotifications: (on: boolean) => void;
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

export const useUIStore = create<UIState>((set, get) => ({
  searchOpen: false,
  searchInitialQuery: "",
  theme: loadTheme(),
  developerMode: loadDeveloperMode(),
  fontSize: loadFontSize(),
  zoom: loadZoom(),
  sendOnEnter: loadBool(SEND_ON_ENTER_KEY, true),
  osNotifications: loadBool(OS_NOTIFICATIONS_KEY, true),

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
  setFontSize: (fontSize) => {
    persistFontSize(fontSize);
    set({ fontSize });
  },
  nudgeZoom: (direction) => {
    const zoom = zoomStep(get().zoom, direction);
    persistZoom(zoom);
    set({ zoom });
  },
  resetZoom: () => {
    persistZoom(ZOOM_DEFAULT);
    set({ zoom: ZOOM_DEFAULT });
  },
  setSendOnEnter: (sendOnEnter) => {
    uiSet(SEND_ON_ENTER_KEY, sendOnEnter);
    set({ sendOnEnter });
  },
  setOsNotifications: (osNotifications) => {
    uiSet(OS_NOTIFICATIONS_KEY, osNotifications);
    set({ osNotifications });
  },
}));
