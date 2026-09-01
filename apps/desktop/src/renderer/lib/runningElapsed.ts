/**
 * Live 用时的墙钟流逝秒数。展示走 `formatDurationSec`，禁止再拼裸秒。
 *
 * 离线 preview / 挂起流若 `startedAt` 来自合成事件时间戳，相对 `Date.now()` 可能算出
 * 千万级秒数。超过 {@link MAX_SANE_RUNNING_ELAPSED_SEC} 时返回 0，调用方据此省略后缀。
 */
export const MAX_SANE_RUNNING_ELAPSED_SEC = 36 * 60 * 60; // 36h

export function runningElapsedSec(
  startedAtMs: number | null | undefined,
  nowMs: number = Date.now(),
): number {
  if (startedAtMs == null || !Number.isFinite(startedAtMs)) return 0;
  const sec = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
  return sec > MAX_SANE_RUNNING_ELAPSED_SEC ? 0 : sec;
}
