/**
 * 恢复时刻本地化（429 / 平台配额闸门）。
 *
 * 时区断言分两层：`formatLocalMoment` 注入一个确定的时区，逐字钉住「8 月 15 日 00:00」这个
 * 格式；成句用例则拿本模块自己渲染出的时刻去拼期望串——跑测试的机器在哪个时区都成立，钉的
 * 是**措辞**，时刻正确性由第一层负责。
 *
 * 成句用例的输入句全部抄自服务端真实触发面（`LLMRateLimitError` / `upstream_rate_limit_error`
 * / `quota.py`），禁止用截短 stub 冒充线上原文。
 */
import { describe, expect, it } from "vitest";
import { formatLocalMoment, withLocalRecoveryMoment } from "../recoveryMoment";

// 线上那句「8 月 14 日 16:00（UTC）」对中国用户其实是次日零点——本次改造要终结的正是它。
const UTC_1600 = "2026-08-14T16:00:00Z";

// BYOK 日额度墙：`LLMRateLimitError` + attested Retry-After > MAX_RETRY_AFTER + credential_source=user
const BYOK_DAY_RESET =
  "上游限流，本回合无法继续。你的服务商额度恢复前重试仍会失败。";
// 来源不明的日额度墙：同上，credential_source 缺省
const UNKNOWN_DAY_RESET =
  "上游限流，本回合无法继续。上游额度恢复前重试仍会失败。";
// 平台日额度墙：`upstream_rate_limit_error` → QUOTA_EXCEEDED
const PLATFORM_DAY_RESET =
  "平台模型额度已用完，本回合无法继续。请等待上游额度恢复，或接入自己的 API Key 立即继续。";
// 平台配额闸门日 token：`quota.py` enforce_quota
const DAILY_TOKEN_GATE =
  "已达每日 token 上限（1,234 / 5,000），额度重置后可继续；或接入自己的 key 继续。";

describe("formatLocalMoment", () => {
  it("按给定时区渲染，UTC 16:00 在北京是次日零点", () => {
    expect(formatLocalMoment(UTC_1600, "Asia/Shanghai")).toBe(
      "8 月 15 日 00:00",
    );
  });

  it("同一时刻在别的时区就是别的钟点（渲染的是本地时刻，不是 UTC）", () => {
    expect(formatLocalMoment(UTC_1600, "UTC")).toBe("8 月 14 日 16:00");
    expect(formatLocalMoment(UTC_1600, "America/Los_Angeles")).toBe(
      "8 月 14 日 09:00",
    );
  });

  it("不带时区参数时走设备本机时区", () => {
    const at = new Date(UTC_1600);
    const hh = String(at.getHours()).padStart(2, "0");
    const mm = String(at.getMinutes()).padStart(2, "0");
    expect(formatLocalMoment(UTC_1600)).toBe(
      `${at.getMonth() + 1} 月 ${at.getDate()} 日 ${hh}:${mm}`,
    );
  });

  it("不标时区名——渲染出来的就是用户自己的钟", () => {
    expect(formatLocalMoment(UTC_1600, "Asia/Shanghai")).not.toContain("UTC");
  });

  it("无值 / 非法输入答不知道，不编一个时间出来", () => {
    expect(formatLocalMoment(null)).toBeNull();
    expect(formatLocalMoment(undefined)).toBeNull();
    expect(formatLocalMoment("")).toBeNull();
    expect(formatLocalMoment("下周三")).toBeNull();
  });
});

describe("withLocalRecoveryMoment", () => {
  const moment = formatLocalMoment(UTC_1600) as string;

  it("拿不到结构化时刻就原样转述服务端那句", () => {
    expect(withLocalRecoveryMoment(BYOK_DAY_RESET, {})).toBe(BYOK_DAY_RESET);
    expect(withLocalRecoveryMoment(BYOK_DAY_RESET, { context: {} })).toBe(
      BYOK_DAY_RESET,
    );
    expect(
      withLocalRecoveryMoment(BYOK_DAY_RESET, {
        context: { recovery_at: null },
      }),
    ).toBe(BYOK_DAY_RESET);
  });

  it("时刻非法时宁可少说一句，也不自己编时间", () => {
    expect(
      withLocalRecoveryMoment(UNKNOWN_DAY_RESET, {
        context: { recovery_at: "明天早上" },
      }),
    ).toBe(UNKNOWN_DAY_RESET);
  });

  it("BYOK 日额度墙：原句保留，「服务商额度」说的是谁的钱，后面只多出时刻", () => {
    const text = withLocalRecoveryMoment(BYOK_DAY_RESET, {
      code: "LLM_RATE_LIMIT",
      context: { recovery_at: UTC_1600, credential_source: "user" },
    });
    expect(text).toBe(`${BYOK_DAY_RESET}额度将于 ${moment} 恢复。`);
    // 旧重写模板把「恢复前重试仍会失败」揉进时刻子句；现在子句只报时刻。
    expect(text).not.toContain("在此之前重试仍会失败");
  });

  it("来源不明的日额度墙：原句是泛指的「上游额度」，不猜是谁的钱", () => {
    expect(
      withLocalRecoveryMoment(UNKNOWN_DAY_RESET, {
        code: "LLM_RATE_LIMIT",
        context: { recovery_at: UTC_1600 },
      }),
    ).toBe(`${UNKNOWN_DAY_RESET}额度将于 ${moment} 恢复。`);
  });

  it("平台额度撞上游墙：原句 + 时刻子句，不再按码整句重写", () => {
    const text = withLocalRecoveryMoment(PLATFORM_DAY_RESET, {
      code: "QUOTA_EXCEEDED",
      context: { recovery_at: UTC_1600, credential_source: "platform" },
    });
    expect(text).toBe(`${PLATFORM_DAY_RESET}额度将于 ${moment} 恢复。`);
    expect(text.startsWith(PLATFORM_DAY_RESET)).toBe(true);
    // 旧重写模板：「上游将于 … 恢复；或接入自己的 API Key…」
    expect(text).not.toContain("上游将于");
  });

  it("配额闸门：保留服务端那句的用量数字，另起一句说重置时刻", () => {
    expect(
      withLocalRecoveryMoment(DAILY_TOKEN_GATE, {
        code: "QUOTA_EXCEEDED",
        context: { reset_at: UTC_1600 },
      }),
    ).toBe(`${DAILY_TOKEN_GATE}额度将于 ${moment} 重置。`);
  });

  it("服务端那句没有句末标点时补一个，不粘成一句", () => {
    expect(
      withLocalRecoveryMoment("本月额度已用完", {
        context: { reset_at: UTC_1600 },
      }),
    ).toBe(`本月额度已用完。额度将于 ${moment} 重置。`);
  });

  it("两个时刻都在时以 recovery_at 为准（上游那堵墙更晚放行）", () => {
    const out = withLocalRecoveryMoment(BYOK_DAY_RESET, {
      code: "LLM_RATE_LIMIT",
      context: { recovery_at: UTC_1600, reset_at: "2026-08-14T00:00:00Z" },
    });
    expect(out).toBe(`${BYOK_DAY_RESET}额度将于 ${moment} 恢复。`);
    expect(out).not.toContain("重置");
  });

  it("渲染结果里不出现时区名", () => {
    expect(
      withLocalRecoveryMoment(BYOK_DAY_RESET, {
        code: "LLM_RATE_LIMIT",
        context: { recovery_at: UTC_1600, credential_source: "user" },
      }),
    ).not.toContain("UTC");
  });
});
