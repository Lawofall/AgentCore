import { detachLocalBrowserHost } from "@/lib/detachLocalBrowserHost";
import { uiGet, uiSet } from "@/lib/uiStorage";
import { useConversationStore } from "../conversation";
import { browserStillInDock } from "./helpers";
import {
  DEFAULT_WIDTH,
  MIN_WIDTH,
  OPEN_KEY,
  type SidePanelGet,
  type SidePanelSet,
  type SidePanelState,
  WIDTH_KEY,
  sidePanelMaxWidth,
} from "./types";

export const clampWidth = (w: number): number =>
  Math.max(MIN_WIDTH, Math.min(sidePanelMaxWidth(), Math.round(w)));

export function loadOpen(): boolean {
  return uiGet<boolean>(OPEN_KEY) === true;
}

export function loadWidth(): number {
  const raw = uiGet<number>(WIDTH_KEY);
  return typeof raw === "number" && Number.isFinite(raw)
    ? clampWidth(raw)
    : DEFAULT_WIDTH;
}

/**
 * 草稿（`currentConversationId == null`）不可用右坞：不出现、不能打开。
 * 有会话后才允许 reveal；进草稿由 `closePanel` / 页面层强制关。
 */
export function canRevealSidePanel(): boolean {
  return useConversationStore.getState().currentConversationId != null;
}

export function persistOpen(open: boolean): void {
  uiSet(OPEN_KEY, open);
}

export function persistWidth(width: number): void {
  uiSet(WIDTH_KEY, width);
}

type ChromeActions = Pick<
  SidePanelState,
  | "openPanel"
  | "closePanel"
  | "togglePanel"
  | "setWidth"
  | "reclampWidth"
  | "cycleWidth"
>;

/** Chrome geometry persistence: open / width load-store + clamp / cycle. */
export function createChromeActions(
  set: SidePanelSet,
  get: SidePanelGet,
): ChromeActions {
  return {
    openPanel: () => {
      if (!canRevealSidePanel()) return;
      persistOpen(true);
      set({ open: true, pendingBadge: 0 });
    },

    closePanel: () => {
      // 关坞只卸右侧槽；floats 保留。detach 仅当 browser 仍在坞内。
      if (browserStillInDock(get().tabs)) {
        void detachLocalBrowserHost();
      }
      persistOpen(false);
      set({ open: false });
    },

    togglePanel: () => {
      const next = !get().open;
      // 草稿不能打开；若异常仍开着则允许关掉。
      if (next && !canRevealSidePanel()) return;
      if (!next && browserStillInDock(get().tabs)) {
        // 关坞 = 脱离保活（仅 browser 仍在坞内时）。
        void detachLocalBrowserHost();
      }
      persistOpen(next);
      set({ open: next, pendingBadge: next ? 0 : get().pendingBadge });
    },

    setWidth: (width) => {
      const clamped = clampWidth(width);
      persistWidth(clamped);
      set({ width: clamped });
    },

    reclampWidth: () => {
      const clamped = clampWidth(get().width);
      if (clamped === get().width) return;
      persistWidth(clamped);
      set({ width: clamped });
    },

    cycleWidth: () => {
      const max = sidePanelMaxWidth();
      // 窄屏时 default 可能 ≥ max，去重避免出现「同一档」的空转停顿。
      const stops = Array.from(
        new Set([MIN_WIDTH, Math.min(DEFAULT_WIDTH, max), max]),
      ).sort((a, b) => a - b);
      const cur = get().width;
      const EPS = 2;
      // 从小到大跳到下一档，到顶回到最小 → min → default → max → min 循环。
      const next = stops.find((s) => s > cur + EPS) ?? stops[0];
      persistWidth(next);
      set({ width: next });
    },
  };
}
