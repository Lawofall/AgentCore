import { describe, expect, it } from "vitest";
import {
  FEATURED_PLAN_ID,
  SEAT_STORE_URL,
  SUBSCRIPTION_PLANS,
  SUBSCRIPTION_PLAN_ORDER,
  SUBSCRIPTION_SHELF_ORDER,
  formatPriceYuan,
  formatSeatUntil,
  matchPublishedSeat,
  planFeatureLines,
  planValuePitch,
  publishedSeatCanUpgrade,
  resolveSeatStoreUrl,
} from "../subscriptionPlans";

describe("subscriptionPlans", () => {
  it("keeps 旗舰 above 高级 on credits, daily cap, and search", () => {
    const plus = SUBSCRIPTION_PLANS.plus;
    const flagship = SUBSCRIPTION_PLANS.flagship;
    expect(flagship.monthlyQuotaYuan).toBeGreaterThan(plus.monthlyQuotaYuan);
    expect(flagship.dailyQuotaYuan).toBeGreaterThan(plus.dailyQuotaYuan);
    expect(flagship.searchDaily).toBeGreaterThan(plus.searchDaily);
    expect(flagship.searchMonthly).toBe(plus.searchMonthly * 2);
  });

  it("lists 免费 then paid 基础 / 高级 / 旗舰", () => {
    expect(SUBSCRIPTION_PLAN_ORDER).toEqual([
      "basic",
      "starter",
      "plus",
      "flagship",
    ]);
    expect(SUBSCRIPTION_SHELF_ORDER).toEqual(["starter", "plus", "flagship"]);
    expect(SUBSCRIPTION_PLANS.basic.name).toBe("免费版");
    expect(SUBSCRIPTION_PLANS.basic.priceYuan).toBe(0);
    expect(SUBSCRIPTION_PLANS.basic.monthlyQuotaYuan).toBe(5);
    expect(SUBSCRIPTION_PLANS.basic.dailyQuotaYuan).toBe(5);
    expect(SUBSCRIPTION_PLANS.starter.name).toBe("基础版");
    expect(SUBSCRIPTION_PLANS.starter.priceYuan).toBe(19.9);
    expect(SUBSCRIPTION_PLANS.starter.monthlyQuotaYuan).toBe(20);
    expect(SUBSCRIPTION_PLANS.plus.priceYuan).toBe(49.9);
    expect(SUBSCRIPTION_PLANS.plus.monthlyQuotaYuan).toBe(55);
    expect(SUBSCRIPTION_PLANS.plus.dailyQuotaYuan).toBe(8);
    expect(SUBSCRIPTION_PLANS.flagship.priceYuan).toBe(89);
    expect(SUBSCRIPTION_PLANS.flagship.monthlyQuotaYuan).toBe(100);
    expect(SUBSCRIPTION_PLANS.flagship.dailyQuotaYuan).toBe(10);
  });

  it("keeps pricing-card copy tied to the SKU", () => {
    expect(FEATURED_PLAN_ID).toBe("plus");
    expect(SUBSCRIPTION_PLANS.basic.tagline).toBe("覆盖日常试用");
    expect(SUBSCRIPTION_PLANS.starter.tagline).toBe("覆盖日常使用");
    expect(SUBSCRIPTION_PLANS.plus.tagline).toBe("支持高频使用");
    expect(SUBSCRIPTION_PLANS.flagship.tagline).toBe("满足重度需求");
    expect(planFeatureLines(SUBSCRIPTION_PLANS.plus)).toEqual([
      "本月上限 55 积分",
      "每日上限 8 积分",
      "搜索 日 150 · 月 1500",
      "全部平台模型",
      "云端、本地、组团、辩论",
    ]);
    expect(planValuePitch(SUBSCRIPTION_PLANS.basic)).toBeNull();
    expect(planValuePitch(SUBSCRIPTION_PLANS.starter)).toBe(
      "DeepSeek 约官网 4 倍用量",
    );
    expect(planValuePitch(SUBSCRIPTION_PLANS.plus)).toBe(
      "DeepSeek 约官网 4 倍用量",
    );
    expect(planValuePitch(SUBSCRIPTION_PLANS.flagship)).toBe(
      "DeepSeek 约官网 4 倍用量",
    );
    expect(formatPriceYuan(19.9)).toBe("19.9");
    expect(formatPriceYuan(49.9)).toBe("49.9");
    expect(formatPriceYuan(89)).toBe("89");
    expect(SEAT_STORE_URL).toBe("https://wzyp.cn/shop/AgentCore");
    expect(resolveSeatStoreUrl("")).toBe(SEAT_STORE_URL);
    expect(resolveSeatStoreUrl("  ")).toBe(SEAT_STORE_URL);
    expect(resolveSeatStoreUrl(undefined)).toBe(SEAT_STORE_URL);
  });

  it("offers a higher seat only below 旗舰 caps", () => {
    const nano = 1_000_000_000;
    expect(
      publishedSeatCanUpgrade({
        monthlyCostNano: 5 * nano,
        dailyCostNano: 5 * nano,
        dailyHero: false,
      }),
    ).toBe(true);
    expect(
      publishedSeatCanUpgrade({
        monthlyCostNano: 100 * nano,
        dailyCostNano: 10 * nano,
        dailyHero: false,
      }),
    ).toBe(false);
    expect(
      publishedSeatCanUpgrade({
        monthlyCostNano: 100 * nano,
        dailyCostNano: 10 * nano,
        dailyHero: true,
      }),
    ).toBe(false);
    expect(
      publishedSeatCanUpgrade({
        monthlyCostNano: 100 * nano,
        dailyCostNano: 5 * nano,
        dailyHero: true,
      }),
    ).toBe(true);
  });

  it("labels a seat only when both cost caps match a published SKU", () => {
    const nano = 1_000_000_000;
    expect(
      matchPublishedSeat({
        monthlyCostNano: 5 * nano,
        dailyCostNano: 5 * nano,
      }),
    ).toBe("basic");
    expect(
      matchPublishedSeat({
        monthlyCostNano: 20 * nano,
        dailyCostNano: 5 * nano,
      }),
    ).toBe("starter");
    expect(
      matchPublishedSeat({
        monthlyCostNano: 55 * nano,
        dailyCostNano: 8 * nano,
      }),
    ).toBe("plus");
    expect(
      matchPublishedSeat({
        monthlyCostNano: 100 * nano,
        dailyCostNano: 10 * nano,
      }),
    ).toBe("flagship");
    expect(
      matchPublishedSeat({
        monthlyCostNano: 49 * nano,
        dailyCostNano: 5 * nano,
      }),
    ).toBeNull();
    expect(
      matchPublishedSeat({
        monthlyCostNano: 55 * nano,
        dailyCostNano: 10 * nano,
      }),
    ).toBeNull();
    expect(
      matchPublishedSeat({ monthlyCostNano: 0, dailyCostNano: 10 * nano }),
    ).toBeNull();
  });

  it("formats a seat expiry in the local calendar", () => {
    expect(formatSeatUntil("not-a-date")).toBe("");
    const label = formatSeatUntil("2026-11-17T16:00:00Z");
    expect(label).toMatch(/2026年/);
    expect(label).toMatch(/月/);
    expect(label).toMatch(/日/);
  });
});
