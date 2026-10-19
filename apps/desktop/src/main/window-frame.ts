import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  WINDOW_CHANNELS,
  WINDOW_FRAME_PRESETS,
  type WindowFramePreset,
} from "@shared/window-contract";
import { BrowserWindow, app, ipcMain, screen } from "electron";

const PRESET_BY_ID = new Map(
  WINDOW_FRAME_PRESETS.map((p) => [p.id, p] as const),
);

function presetFilePath(): string {
  return join(app.getPath("userData"), "window-frame-preset.json");
}

function readSavedPreset(): WindowFramePreset {
  try {
    const raw = JSON.parse(readFileSync(presetFilePath(), "utf-8")) as {
      preset?: unknown;
    };
    if (raw.preset === "free") return "free";
    if (
      typeof raw.preset === "string" &&
      PRESET_BY_ID.has(raw.preset as never)
    ) {
      return raw.preset as WindowFramePreset;
    }
  } catch {
    // 首次 / 损坏 → 自由缩放。
  }
  return "free";
}

function writeSavedPreset(preset: WindowFramePreset): void {
  try {
    writeFileSync(
      presetFilePath(),
      JSON.stringify({ preset }, null, 2),
      "utf-8",
    );
  } catch (e) {
    console.error("[window-frame] 持久化拍摄比例失败:", e);
  }
}

/** 若预设大于当前屏工作区，等比缩小以完整落入。 */
export function fitToWorkArea(
  width: number,
  height: number,
  workArea: { width: number; height: number },
): { width: number; height: number } {
  if (width <= workArea.width && height <= workArea.height) {
    return { width, height };
  }
  const scale = Math.min(workArea.width / width, workArea.height / height, 1);
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function centerOnDisplay(
  width: number,
  height: number,
): { x: number; y: number; width: number; height: number } {
  const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  const { workArea } = display;
  const fitted = fitToWorkArea(width, height, workArea);
  return {
    width: fitted.width,
    height: fitted.height,
    x: Math.round(workArea.x + (workArea.width - fitted.width) / 2),
    y: Math.round(workArea.y + (workArea.height - fitted.height) / 2),
  };
}

function isWindowFramePreset(value: unknown): value is WindowFramePreset {
  return (
    value === "free" ||
    (typeof value === "string" && PRESET_BY_ID.has(value as never))
  );
}

let currentPreset: WindowFramePreset = "free";
let frameIpcRegistered = false;

export function getFramePreset(): WindowFramePreset {
  return currentPreset;
}

/** 打包安装包不恢复拍摄锁：标题栏没有解锁入口，避免开发态留下的比例把用户窗锁死。 */
export function resolveStartupFramePreset(
  saved: WindowFramePreset,
  packaged: boolean,
): WindowFramePreset {
  return packaged ? "free" : saved;
}

/** 对已有窗口应用拍摄比例（启动恢复与 IPC 共用）。 */
export function applyFramePresetToWindow(
  window: BrowserWindow,
  preset: WindowFramePreset,
): void {
  currentPreset = preset;
  writeSavedPreset(preset);

  if (preset === "free") {
    window.setAspectRatio(0);
    return;
  }

  const info = PRESET_BY_ID.get(preset);
  if (!info) return;

  if (window.isMaximized()) window.unmaximize();

  const bounds = centerOnDisplay(info.width, info.height);
  window.setBounds(bounds);
  window.setAspectRatio(info.aspect);
}

/**
 * 注册拍摄比例 IPC，并在未打包时恢复上次选择（若有）。
 * apply 按 sender 窗路由（主窗 / 真 OS 浮窗均可）；IPC 句柄全局只注册一次。
 */
export function registerWindowFrameIpc(window: BrowserWindow): void {
  currentPreset = resolveStartupFramePreset(readSavedPreset(), app.isPackaged);

  if (!frameIpcRegistered) {
    frameIpcRegistered = true;
    ipcMain.handle(WINDOW_CHANNELS.applyFramePreset, (e, raw: unknown) => {
      if (!isWindowFramePreset(raw)) return;
      const win = BrowserWindow.fromWebContents(e.sender);
      if (!win || win.isDestroyed()) return;
      applyFramePresetToWindow(win, raw);
    });

    ipcMain.handle(WINDOW_CHANNELS.getFramePreset, () => currentPreset);
  }

  if (currentPreset !== "free") {
    window.once("ready-to-show", () => {
      applyFramePresetToWindow(window, currentPreset);
    });
  }
}
