/**
 * 展示口径的格式化函数（金额 / 计数）。
 *
 * 总览首屏把「今日成本 ¥1234567.89」摆在「今日活跃用户 1,284」旁边：同一屏两套数字
 * 写法，偏偏是最容易看错量级的金额没有千分位。这里钉住分位分组，同时守住金额原有的
 * 语义——符号取自金额自带的币种、不足 1 分不写 0、已知的 0 仍写「¥0.00」。
 */

import {
  fmtCount,
  fmtEstimatedMoney,
  fmtInt,
  fmtMoney,
  fmtNanoMoney,
} from "@/lib/utils";
import { describe, expect, it } from "vitest";

describe("fmtMoney", () => {
  it("大额带千分位，与计数（fmtInt）同一套分组", () => {
    expect(fmtMoney(1234567.89)).toBe("¥1,234,567.89");
    expect(fmtInt(1234567)).toBe("1,234,567");
  });

  it("小额与两位小数照旧", () => {
    expect(fmtMoney(12.5)).toBe("¥12.50");
    expect(fmtMoney(999.999)).toBe("¥1,000.00");
  });

  it("已知的 0 写「¥0.00」，有花销但不足 1 分写「<¥0.01」", () => {
    expect(fmtMoney(0)).toBe("¥0.00");
    expect(fmtMoney(0.004)).toBe("<¥0.01");
  });

  it("符号跟随金额自带的币种，未知币种不冒充 ¥", () => {
    expect(fmtMoney(1234567.89, "USD")).toBe("$1,234,567.89");
    expect(fmtMoney(0.004, "USD")).toBe("<$0.01");
    expect(fmtMoney(1200, "EUR")).toBe("EUR 1,200.00");
  });

  it("nano 与估算金额沿用同一分位口径", () => {
    expect(fmtNanoMoney(1_234_567_890_000_000)).toBe("¥1,234,567.89");
    expect(fmtNanoMoney(0)).toBe("—");
    expect(fmtEstimatedMoney(1234567.89, "USD")).toBe("≈$1,234,567.89");
    expect(fmtEstimatedMoney(0)).toBe("—");
  });
});

describe("fmtCount", () => {
  it("拿到过总数才写数字（带千分位）", () => {
    expect(fmtCount(118273, true)).toBe("118,273");
    expect(fmtCount(0, true)).toBe("0");
  });

  it("总数未知时写「—」，不写 0", () => {
    expect(fmtCount(0, false)).toBe("—");
  });
});
