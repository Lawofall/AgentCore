/**
 * 后台进程输出：strip ANSI（MVP 不引 xterm；终端 tab 纯文本滚屏）。
 */

import { formatDuration } from "./format";

/** CSI / OSC 等常见 ESC 序列（用 String.fromCharCode 避开 regex 控制字符 lint）。 */
const ESC = String.fromCharCode(0x1b);
const BEL = String.fromCharCode(0x07);
const ANSI_RE = new RegExp(
  `${ESC}(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~]|\\][^${BEL}]*(?:${BEL}|${ESC}\\\\))`,
  "g",
);

export function stripAnsi(text: string): string {
  return text.replace(ANSI_RE, "");
}

/** UI 侧输出截断（保留尾部），与主进程环形 buffer 同量级。 */
export const UI_OUTPUT_CAP = 1024 * 1024;

export function appendUiOutput(
  current: string,
  chunk: string,
  cap = UI_OUTPUT_CAP,
): string {
  if (!chunk) return current;
  const next = current + chunk;
  if (next.length <= cap) return next;
  return next.slice(next.length - cap);
}

/**
 * 终端 tab 条件显隐：本对话有后台进程 / 执行记录 / 用户终端，或可新开交互 shell。
 */
export function shouldShowTerminalTab(
  processCount: number,
  recordCount = 0,
  ptyCount = 0,
  canOpenPty = false,
): boolean {
  return processCount > 0 || recordCount > 0 || ptyCount > 0 || canOpenPty;
}

/** 进程已运行多久：与任务用时同一套 {@link formatDuration}（秒数仍按墙钟向下取整，避免半秒跳字）。 */
export function formatProcessDuration(
  startedAt: string,
  nowMs = Date.now(),
): string {
  const start = Date.parse(startedAt);
  if (!Number.isFinite(start)) return "—";
  const sec = Math.max(0, Math.floor((nowMs - start) / 1000));
  return formatDuration(sec * 1000);
}
