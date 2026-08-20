import { uiGet, uiSet } from "@/lib/uiStorage";
import { create } from "zustand";

const DIAGNOSTIC_MODE_KEY = "diagnostic-mode";
const THEME_KEY = "theme";
const SIDECAR_KEY = "sidecar-enabled";

type Theme = "light" | "dark" | "system";

// 开发者 / 诊断模式 (前端UX设计.md §十): off by default.
function loadDiagnosticMode(): boolean {
  return uiGet<boolean>(DIAGNOSTIC_MODE_KEY) === true;
}

function persistDiagnosticMode(v: boolean): void {
  uiSet(DIAGNOSTIC_MODE_KEY, v);
}

// Theme is persisted so the choice survives a reload; it is *applied* to the DOM
// by lib/theme.ts (the store only holds the value). Falls back to 跟随系统.
function loadTheme(): Theme {
  const v = uiGet<string>(THEME_KEY);
  return v === "light" || v === "dark" || v === "system" ? v : "system";
}

function persistTheme(v: Theme): void {
  uiSet(THEME_KEY, v);
}

// 本机执行（sidecar）偏好——三态（双模式工作区 §7.2）。
//
// 产品：本机传统（mode=local）新开回合默认同侧 sidecar。大众 Appearance **不再**暴露本开关；
// 强制关（偏好 `off`）仅诊断入口可写——不是「默认关→整段过桥」。
//   - `setSidecarEnabled(true)` → 偏好 `on`（允许本机同侧；与 unset 对路由等价）
//   - `setSidecarEnabled(false)` → 偏好 `off`（显式强制走云，诊断用）
// 持久化的是**偏好**而非有效值，故翻产品默认时不静默改写已落盘的 `on`/`off`：
//   - "unset"（无 key）→ 跟随 `SIDECAR_DEFAULT_ENABLED`（仅影响 {@link sidecarEnabled} 展示布尔；
//     路由以 `sidecarPreference === "off"` 为强制关，unset **不**挡本机传统）
//   - "on" / "off" → 显式选择，恒被尊重（历史大众路径落盘的 `off` 见一次性迁移）
// 新开回合路由读 `sidecarPreference`（见 `isSidecarForceOff`），勿把 `sidecarEnabled` 当默认挡板。
type SidecarPreference = "unset" | "on" | "off";

/** 一次性清历史：大众 Appearance 曾可写 `off`；毕业后加载时把历史 off→unset，之后诊断仍可显式写 off。 */
const SIDECAR_OFF_CLEARED_KEY = "sidecar-off-cleared-v1";

/** 未表态时 {@link sidecarEnabled} 布尔默认。保持 false——**勿**翻成全站 true 当「默认同侧」捷径；
 * 本机传统默认同侧由 `resolveSidecarRoot` 按绑定判定，与本常量解耦。"unset" 跟随此默认记入
 * `sidecarEnabled`；显式 "on"/"off" 不受影响，勿静默改写已落盘偏好。 */
const SIDECAR_DEFAULT_ENABLED = false;

/**
 * 解析持久化偏好。三态字符串为主；兼容毕业前 boolean 落盘：
 * `false` = 用户显式关过 → `off`（勿当 unset，否则翻默认时误伤显式选择）；
 * `true` = 显式开过 → `on`。无 key / 其它值 → `unset`（跟产品默认）。
 */
export function parseSidecarPreference(raw: unknown): SidecarPreference {
  if (raw === "on" || raw === true) return "on";
  if (raw === "off" || raw === false) return "off";
  return "unset";
}

/**
 * 加载本机执行偏好。尚未写过 {@link SIDECAR_OFF_CLEARED_KEY} 时做一次性迁移：
 * 已落盘 `off`（含旧 boolean `false` 经 {@link parseSidecarPreference}）删键 → `unset`，并写 flag。
 * 之后诊断入口 `setSidecarEnabled(false)` 再写的 `off` 不会被二次清掉。
 */
export function loadSidecarPreference(): SidecarPreference {
  const pref = parseSidecarPreference(uiGet<unknown>(SIDECAR_KEY));
  if (uiGet<boolean>(SIDECAR_OFF_CLEARED_KEY) === true) return pref;
  uiSet(SIDECAR_OFF_CLEARED_KEY, true);
  if (pref !== "off") return pref;
  uiSet(SIDECAR_KEY, undefined);
  return "unset";
}

function persistSidecarPreference(p: "on" | "off"): void {
  uiSet(SIDECAR_KEY, p);
}

/** 有效开关值：未表态时取产品默认，否则取用户显式选择。 */
function resolveSidecarEnabled(pref: SidecarPreference): boolean {
  return pref === "unset" ? SIDECAR_DEFAULT_ENABLED : pref === "on";
}

function loadSidecarEnabled(): boolean {
  return resolveSidecarEnabled(loadSidecarPreference());
}

interface UIState {
  searchOpen: boolean;
  /** Prefill for the next palette open; consumed on open. */
  searchInitialQuery: string;
  /** Open directly in the bookmarks facet (命令面板「已收藏」); consumed on open. */
  searchInitialBookmarks: boolean;
  theme: Theme;
  /** 开发者 / 诊断模式 (前端UX设计.md §十). When true, low-level execution
   * diagnostics (run / trace ids、调度埋点等) surface in run detail — dev-only
   * noise kept off the 大众 path. 「复制排查包」(错误卡 / 气泡更多) 不依赖本开关。
   * Persisted via uiStorage (`agentcore:diagnostic-mode`). */
  diagnosticMode: boolean;
  /** 本机执行偏好折成的展示布尔（= `resolveSidecarEnabled`；unset→`SIDECAR_DEFAULT_ENABLED`）。
   * **不是**新开回合路由挡板——路由看 {@link sidecarPreference} 是否显式 `off`（强制关）。
   * 设置面应用 `preference !== "off"` 表示「允许」，以免 unset 显示关却仍默认同侧。 */
  sidecarEnabled: boolean;
  /** 本机执行**持久化偏好**（三态）：`unset` / `on` = 允许本机传统同侧；`off` = 诊断强制走云。
   * 翻产品默认时不静默改写已落盘偏好。持久化到 `agentcore:sidecar-enabled`。
   * 大众 Appearance 无此开关；强制关仅诊断入口。 */
  sidecarPreference: SidecarPreference;

  openSearch: (initialQuery?: string, opts?: { bookmarks?: boolean }) => void;
  closeSearch: () => void;
  toggleSearch: () => void;
  setTheme: (theme: UIState["theme"]) => void;
  setDiagnosticMode: (v: boolean) => void;
  toggleDiagnosticMode: () => void;
  setSidecarEnabled: (v: boolean) => void;
}

/** Full-screen turn detail view (`#/conversations/:id/turn/:turnId?view=`). */
export type TurnDetailView = "graph" | "debate" | "compare";

/** Build the hash-route path for a turn's full-screen detail page. */
export function turnDetailPath(
  conversationId: string,
  turnId: string,
  view?: TurnDetailView,
  comparePair?: [string, string],
  opts?: { autoplay?: boolean },
): string {
  const path = `/conversations/${conversationId}/turn/${turnId}`;
  const params = new URLSearchParams();
  if (view) params.set("view", view);
  if (comparePair) {
    params.set("a", comparePair[0]);
    params.set("b", comparePair[1]);
  }
  if (opts?.autoplay) params.set("autoplay", "1");
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export const useUIStore = create<UIState>((set) => ({
  searchOpen: false,
  searchInitialQuery: "",
  searchInitialBookmarks: false,
  theme: loadTheme(),
  diagnosticMode: loadDiagnosticMode(),
  sidecarPreference: loadSidecarPreference(),
  sidecarEnabled: loadSidecarEnabled(),

  // Default "" is required: Sidebar/TitleBar call openSearch() with no args.
  // Without it, searchInitialQuery becomes undefined and CommandPalette crashes
  // on query.trim() (regressed in 1ee81cee when the default was dropped).
  openSearch: (initialQuery, opts) =>
    set({
      searchOpen: true,
      searchInitialQuery: initialQuery ?? "",
      searchInitialBookmarks: opts?.bookmarks ?? false,
    }),
  closeSearch: () =>
    set({
      searchOpen: false,
      searchInitialQuery: "",
      searchInitialBookmarks: false,
    }),
  toggleSearch: () => set((s) => ({ searchOpen: !s.searchOpen })),
  setTheme: (theme) => {
    persistTheme(theme);
    set({ theme });
  },
  setDiagnosticMode: (diagnosticMode) => {
    persistDiagnosticMode(diagnosticMode);
    set({ diagnosticMode });
  },
  toggleDiagnosticMode: () =>
    set((s) => {
      const diagnosticMode = !s.diagnosticMode;
      persistDiagnosticMode(diagnosticMode);
      return { diagnosticMode };
    }),
  setSidecarEnabled: (sidecarEnabled) => {
    const sidecarPreference: SidecarPreference = sidecarEnabled ? "on" : "off";
    persistSidecarPreference(sidecarPreference);
    set({ sidecarEnabled, sidecarPreference });
  },
}));
