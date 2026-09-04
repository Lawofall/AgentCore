/**
 * 主窗口拍摄比例 IPC 契约 —— 供 OBS / 系统录屏时一键跳到 16:9、4:3 标准尺寸并锁定比例。
 *
 * 与 minimize/maximize/close 同属 `windowApi`，但走 invoke/handle（需返回值与校验）。
 */

/** 预设 id；`free` = 解除比例锁，恢复自由缩放。 */
export type WindowFramePreset = "free" | "16:9-1080" | "4:3-uxga";

export interface WindowFramePresetInfo {
  id: Exclude<WindowFramePreset, "free">;
  label: string;
  width: number;
  height: number;
  aspect: number;
}

/** 渲染层菜单展示用；尺寸与主进程 `window-frame.ts` 保持同步。 */
export const WINDOW_FRAME_PRESETS: readonly WindowFramePresetInfo[] = [
  {
    id: "16:9-1080",
    label: "16:9 · 1920×1080",
    width: 1920,
    height: 1080,
    aspect: 16 / 9,
  },
  {
    id: "4:3-uxga",
    label: "4:3 · 1600×1200",
    width: 1600,
    height: 1200,
    aspect: 4 / 3,
  },
] as const;

/** Click a ratio: lock it from free, switch if another is locked, unlock if it is already active. */
export function toggleWindowFramePreset(
  current: WindowFramePreset,
  clicked: Exclude<WindowFramePreset, "free">,
): WindowFramePreset {
  return current === clicked ? "free" : clicked;
}

export const WINDOW_CHANNELS = {
  applyFramePreset: "window:applyFramePreset",
  getFramePreset: "window:getFramePreset",
  minimize: "window:minimize",
  maximize: "window:maximize",
  close: "window:close",
  setThemeSource: "window:setThemeSource",
} as const;

export type WindowThemeSource = "light" | "dark" | "system";

export interface WindowApi {
  minimize: () => void;
  maximize: () => void;
  close: () => void;
  /** 跳到预设外框尺寸并锁定比例；`free` 解除锁定。最大化时先还原。 */
  applyFramePreset: (preset: WindowFramePreset) => Promise<void>;
  getFramePreset: () => Promise<WindowFramePreset>;
  /** 同步 Electron `nativeTheme`（标题栏 / 系统控件）到渲染层主题。 */
  setThemeSource: (theme: WindowThemeSource) => void;
}
