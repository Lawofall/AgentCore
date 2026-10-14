import { ApiError } from "@/services/api";
import { useEffect, useMemo, useState } from "react";

/** Whole seconds until ``untilMs``; 0 when the instant is missing or already past. */
export function remainingSeconds(untilMs: number | null, now: number): number {
  if (untilMs == null) return 0;
  return Math.max(0, Math.ceil((untilMs - now) / 1000));
}

/** Visible wait, e.g. ``14 分 32 秒`` / ``32 秒`` / ``1 分钟``. */
export function formatLockWait(seconds: number): string {
  const total = Math.max(0, Math.ceil(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    if (minutes === 0 && secs === 0) return `${hours} 小时`;
    if (secs === 0) return `${hours} 小时 ${minutes} 分钟`;
    return `${hours} 小时 ${minutes} 分 ${secs} 秒`;
  }
  if (minutes <= 0) return `${secs} 秒`;
  if (secs === 0) return `${minutes} 分钟`;
  return `${minutes} 分 ${secs} 秒`;
}

/** Compact button label, e.g. ``14:32 后可登录``. */
export function lockButtonLabel(seconds: number): string {
  const total = Math.max(0, Math.ceil(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  const clock =
    hours > 0
      ? `${hours}:${pad(minutes)}:${pad(secs)}`
      : `${pad(minutes)}:${pad(secs)}`;
  return `${clock} 后可登录`;
}

export function lockNoticeCopy(seconds: number, hint?: string): string {
  const base = `账户已临时锁定，请 ${formatLockWait(seconds)}后再试`;
  return hint ? `${base}。${hint}` : base;
}

export const LOGIN_LOCK_RESET_HINT =
  "忘记密码可重置，重置后即可登录";

/**
 * Unlock instant from a login error. ``recovery_at`` is the server's clock;
 * ``Retry-After`` is only a fallback when the body named a lockout but omitted
 * the instant (older responses, or a header the browser could still read).
 */
export function lockUntilFromApiError(err: unknown): number | null {
  if (!(err instanceof ApiError)) return null;
  const at = err.recoveryMoment?.recovery_at;
  if (typeof at === "string" && at) {
    const ms = Date.parse(at);
    if (Number.isFinite(ms)) return ms;
  }
  if (
    err.serverMessage?.includes("临时锁定") &&
    err.retryAfter != null &&
    err.retryAfter > 0
  ) {
    return Date.now() + Math.ceil(err.retryAfter) * 1000;
  }
  return null;
}

/** Re-render until ``untilMs``. ``null`` means there is nothing to wait out. */
export function useUnlockCountdown(untilMs: number | null): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (untilMs == null || untilMs <= Date.now()) return;
    const id = window.setInterval(() => {
      setTick((n) => n + 1);
      if (Date.now() >= untilMs) window.clearInterval(id);
    }, 250);
    return () => window.clearInterval(id);
  }, [untilMs]);
  return useMemo(
    () => remainingSeconds(untilMs, Date.now()),
    [untilMs, tick],
  );
}
