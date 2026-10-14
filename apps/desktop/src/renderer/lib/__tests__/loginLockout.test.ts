// @vitest-environment jsdom
import { ApiError } from "@/services/api";
import { act, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  formatLockWait,
  lockButtonLabel,
  lockNoticeCopy,
  lockUntilFromApiError,
  remainingSeconds,
  useUnlockCountdown,
} from "../loginLockout";

afterEach(() => {
  vi.useRealTimers();
});

describe("login lockout copy", () => {
  it("formats a short wait in minutes and seconds", () => {
    expect(formatLockWait(90)).toBe("1 分 30 秒");
    expect(formatLockWait(60)).toBe("1 分钟");
    expect(formatLockWait(32)).toBe("32 秒");
    expect(formatLockWait(15 * 60)).toBe("15 分钟");
    expect(lockButtonLabel(90)).toBe("01:30 后可登录");
    expect(lockButtonLabel(15 * 60)).toBe("15:00 后可登录");
    expect(lockNoticeCopy(90, "忘记密码可重置，重置后即可登录")).toBe(
      "账户已临时锁定，请 1 分 30 秒后再试。忘记密码可重置，重置后即可登录",
    );
  });

  it("reads the unlock instant from the error body", () => {
    const until = Date.parse("2026-10-14T09:15:00Z");
    const err = new ApiError(
      401,
      JSON.stringify({
        error: {
          code: "AUTH_ERROR",
          message: "账户已临时锁定，请稍后再试",
          recovery_at: "2026-10-14T09:15:00Z",
        },
      }),
    );
    expect(lockUntilFromApiError(err)).toBe(until);
    expect(remainingSeconds(until, until - 1500)).toBe(2);
    expect(remainingSeconds(until, until + 1000)).toBe(0);
  });

  it("falls back to Retry-After only for a lockout message", () => {
    const before = Date.now();
    const locked = new ApiError(
      401,
      JSON.stringify({
        error: { code: "AUTH_ERROR", message: "账户已临时锁定，请稍后再试" },
      }),
      new Headers({ "Retry-After": "90" }),
    );
    const until = lockUntilFromApiError(locked);
    expect(until).not.toBeNull();
    expect((until ?? 0) - before).toBeGreaterThanOrEqual(90_000);
    expect((until ?? 0) - before).toBeLessThan(91_000);

    const rate = new ApiError(
      429,
      JSON.stringify({
        error: { code: "RATE_LIMITED", message: "发送过于频繁，请稍后再试" },
      }),
      new Headers({ "Retry-After": "60" }),
    );
    expect(lockUntilFromApiError(rate)).toBeNull();
  });

  it("ticks down to zero and then stops", async () => {
    vi.useFakeTimers();
    const until = Date.now() + 2000;

    function Probe() {
      const left = useUnlockCountdown(until);
      return createElement("span", null, String(left));
    }

    render(createElement(Probe));
    expect(screen.getByText("2")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText("1")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText("0")).toBeTruthy();
  });
});
