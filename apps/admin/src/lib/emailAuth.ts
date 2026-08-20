export const MIN_USERNAME_LENGTH = 3;

export function isLikelyEmail(value: string): boolean {
  const trimmed = value.trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed);
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

export function passwordFieldError(value: string): string | null {
  if (!value) return "请输入密码";
  return null;
}
