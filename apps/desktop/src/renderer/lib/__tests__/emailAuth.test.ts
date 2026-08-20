import { describe, expect, it } from "vitest";
import {
  formatEmailCodeSentNotice,
  isGeneratedHandle,
  isLikelyEmail,
  loginIdentifierError,
  normalizeEmailCode,
  normalizeEmailCodeExpiresIn,
  usernameFieldError,
} from "../emailAuth";

describe("emailAuth helpers", () => {
  it("accepts a typical email and rejects junk", () => {
    expect(isLikelyEmail("alice@example.com")).toBe(true);
    expect(isLikelyEmail("  alice@example.com  ")).toBe(true);
    expect(isLikelyEmail("alice")).toBe(false);
    expect(isLikelyEmail("alice@")).toBe(false);
  });

  it("keeps only 6 digits for the code", () => {
    expect(normalizeEmailCode("12a34-56789")).toBe("123456");
  });

  it("explains login identifier and generated handles", () => {
    expect(loginIdentifierError("")).toBe("请输入邮箱或用户名");
    expect(loginIdentifierError("ab")).toBe("用户名至少 3 个字符");
    expect(loginIdentifierError("ab@x")).toBe("请输入有效邮箱");
    expect(loginIdentifierError("alice@example.com")).toBeNull();
    expect(isGeneratedHandle("user_a3f90d12", "user_a3f90d12")).toBe(true);
    expect(isGeneratedHandle("alice", "alice")).toBe(false);
  });

  it("validates claimable usernames", () => {
    expect(usernameFieldError("")).toBe("请输入用户名");
    expect(usernameFieldError("ab")).toBe("用户名至少 3 个字符");
    expect(usernameFieldError("a".repeat(33))).toBe("用户名最多 32 个字符");
    expect(usernameFieldError("alice@x")).toBe("用户名不能包含 @");
    expect(usernameFieldError("user_deadbeef")).toBe("用户名不能以 user_ 开头");
    expect(usernameFieldError("alice!")).toBe(
      "用户名只能包含字母、数字、_ . -",
    );
    expect(usernameFieldError("_alice")).toBe("用户名首尾须为字母或数字");
    expect(usernameFieldError("admin")).toBe("该用户名不可用");
    expect(usernameFieldError("alice")).toBeNull();
  });

  it("normalizes register code TTL and formats the sent notice", () => {
    expect(normalizeEmailCodeExpiresIn(600)).toBe(600);
    expect(normalizeEmailCodeExpiresIn(undefined)).toBe(600);
    expect(normalizeEmailCodeExpiresIn(-1)).toBe(600);
    expect(formatEmailCodeSentNotice(600)).toBe("已发送验证码，10 分钟内有效");
    expect(formatEmailCodeSentNotice(45)).toBe("已发送验证码，45 秒内有效");
    expect(formatEmailCodeSentNotice(3600)).toBe("已发送验证码，1 小时内有效");
    expect(formatEmailCodeSentNotice(600, true)).toBe(
      "已重新发送验证码，10 分钟内有效",
    );
  });
});
