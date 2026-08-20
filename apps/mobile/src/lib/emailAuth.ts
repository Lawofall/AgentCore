import { useCallback, useEffect, useState } from "react";

/** 6-digit email OTP. Matches the register / reset / catch-up verify contracts. */
export const EMAIL_CODE_LENGTH = 6;
export const RESEND_COOLDOWN_SEC = 60;
/** Fallback when send-code omits or returns an invalid `expires_in` (matches server default). */
export const DEFAULT_EMAIL_CODE_TTL_SEC = 600;
export const MIN_USERNAME_LENGTH = 3;
export const MAX_USERNAME_LENGTH = 32;
export const MIN_PASSWORD_LENGTH = 8;
/** Server `generate_username_handle`: `user_` + 8 hex chars. */
export const GENERATED_HANDLE_RE = /^user_[a-f0-9]{8}$/i;
const USERNAME_CHARS_RE = /^[a-z0-9_.-]+$/;
const USERNAME_EDGE_RE = /^[a-z0-9].*[a-z0-9]$|^[a-z0-9]$/;
const RESERVED_USERNAMES = new Set([
  "admin",
  "official",
  "agentcore",
  "support",
  "system",
]);

export function isLikelyEmail(value: string): boolean {
  const trimmed = value.trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed);
}

export function normalizeEmailCode(value: string): string {
  return value.replace(/\D/g, "").slice(0, EMAIL_CODE_LENGTH);
}

export function loginIdentifierError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "请输入邮箱或用户名";
  if (trimmed.includes("@")) {
    return isLikelyEmail(trimmed) ? null : "请输入有效邮箱";
  }
  if (trimmed.length < MIN_USERNAME_LENGTH) {
    return `用户名至少 ${MIN_USERNAME_LENGTH} 个字符`;
  }
  return null;
}

export function emailFieldError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "请输入邮箱";
  return isLikelyEmail(trimmed) ? null : "请输入有效邮箱";
}

export function passwordFieldError(
  value: string,
  minLength: number,
  emptyMessage = "请输入密码",
): string | null {
  if (!value) return emptyMessage;
  if (value.length < minLength) return `密码至少 ${minLength} 位`;
  return null;
}

export function emailCodeError(value: string): string | null {
  if (value.length === EMAIL_CODE_LENGTH) return null;
  return "请输入 6 位验证码";
}

export function isSystemUsernameHandle(username: string): boolean {
  return GENERATED_HANDLE_RE.test(username.trim());
}

export function isGeneratedHandle(
  username: string,
  displayName: string,
): boolean {
  const u = username.trim();
  const d = displayName.trim();
  return u.length > 0 && d === u && isSystemUsernameHandle(u);
}

export function usernameFieldError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "请输入用户名";
  const normalized = trimmed.toLowerCase();
  if (normalized.length < MIN_USERNAME_LENGTH) {
    return `用户名至少 ${MIN_USERNAME_LENGTH} 个字符`;
  }
  if (normalized.length > MAX_USERNAME_LENGTH) {
    return `用户名最多 ${MAX_USERNAME_LENGTH} 个字符`;
  }
  if (normalized.includes("@")) return "用户名不能包含 @";
  if (normalized.startsWith("user_")) return "用户名不能以 user_ 开头";
  if (!USERNAME_CHARS_RE.test(normalized)) {
    return "用户名只能包含字母、数字、_、.、-";
  }
  if (!USERNAME_EDGE_RE.test(normalized)) {
    return "用户名首尾须为字母或数字";
  }
  if (RESERVED_USERNAMES.has(normalized)) return "该用户名不可用";
  return null;
}

export function normalizeEmailCodeExpiresIn(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return Math.round(value);
  }
  return DEFAULT_EMAIL_CODE_TTL_SEC;
}

function round1(n: number): string {
  const r = Math.round(n * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

function emailCodeValidityWindow(seconds: number): string {
  if (seconds < 60) return `${round1(seconds)} 秒`;
  if (seconds < 3600) return `${round1(seconds / 60)} 分钟`;
  return `${round1(seconds / 3600)} 小时`;
}

/** Register code TTL hint — keep wording identical on desktop and mobile. */
export function formatEmailCodeValidityHint(seconds: number): string {
  const ttl = normalizeEmailCodeExpiresIn(seconds);
  return `验证码 ${emailCodeValidityWindow(ttl)}内有效`;
}

export function useResendCountdown(seconds = RESEND_COOLDOWN_SEC) {
  const [left, setLeft] = useState(0);

  useEffect(() => {
    if (left <= 0) return;
    const id = window.setInterval(() => {
      setLeft((n) => (n <= 1 ? 0 : n - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [left]);

  const start = useCallback(() => setLeft(seconds), [seconds]);

  return { left, start, canResend: left === 0 };
}
