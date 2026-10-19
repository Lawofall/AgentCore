/**
 * Published seat catalog (设置 · 订阅). Numbers are the product SKU, not a
 * wallet. Paid seats are redemption codes; the user row keeps `seat_plan` /
 * `seat_until`. `basic` is the free default, not a paid SKU.
 *
 * Authority → 成本配额与计费.md §〇·六 套餐.
 * monthlyQuotaYuan / dailyQuotaYuan are selling-CNY caps (1 积分 = ¥1 卖价).
 */
export type SubscriptionPlanId = "basic" | "starter" | "plus" | "flagship";
export type PaidPlanId = "starter" | "plus" | "flagship";

export interface SubscriptionPlan {
  id: SubscriptionPlanId;
  name: string;
  /** One-line audience, shown under the name on the pricing card. */
  tagline: string;
  /** 0 = 免费版. */
  priceYuan: number;
  monthlyQuotaYuan: number;
  dailyQuotaYuan: number;
  searchDaily: number;
  searchMonthly: number;
}

export const SUBSCRIPTION_PLANS: Record<SubscriptionPlanId, SubscriptionPlan> =
  {
    basic: {
      id: "basic",
      name: "免费版",
      tagline: "覆盖日常试用",
      priceYuan: 0,
      monthlyQuotaYuan: 5,
      dailyQuotaYuan: 5,
      searchDaily: 40,
      searchMonthly: 400,
    },
    starter: {
      id: "starter",
      name: "基础版",
      tagline: "覆盖日常使用",
      priceYuan: 19.9,
      monthlyQuotaYuan: 20,
      dailyQuotaYuan: 5,
      searchDaily: 80,
      searchMonthly: 800,
    },
    plus: {
      id: "plus",
      name: "高级版",
      tagline: "支持高频使用",
      priceYuan: 49.9,
      monthlyQuotaYuan: 55,
      dailyQuotaYuan: 8,
      searchDaily: 150,
      searchMonthly: 1500,
    },
    flagship: {
      id: "flagship",
      name: "旗舰版",
      tagline: "满足重度需求",
      priceYuan: 89,
      monthlyQuotaYuan: 100,
      dailyQuotaYuan: 10,
      searchDaily: 300,
      searchMonthly: 3000,
    },
  };

export const SUBSCRIPTION_PLAN_ORDER: readonly SubscriptionPlanId[] = [
  "basic",
  "starter",
  "plus",
  "flagship",
] as const;

/** 设置 · 订阅货架：三张付费卡一列。免费档不占卡。 */
export const SUBSCRIPTION_SHELF_ORDER: readonly PaidPlanId[] = [
  "starter",
  "plus",
  "flagship",
] as const;

/** Middle paid seat — the conversion target on the pricing row. */
export const FEATURED_PLAN_ID: PaidPlanId = "plus";

/** 链动小铺。三档同一入口；接口没带回或空着也走这里，不画「未配置」。 */
export const SEAT_STORE_URL = "https://wzyp.cn/shop/AgentCore";

export function resolveSeatStoreUrl(raw: string | null | undefined): string {
  const trimmed = raw?.trim() ?? "";
  return trimmed || SEAT_STORE_URL;
}

/** Card price: 19.9 not 19.90; 89 not 89.0. */
export function formatPriceYuan(priceYuan: number): string {
  return Number.isInteger(priceYuan) ? String(priceYuan) : priceYuan.toFixed(1);
}

/**
 * Price-line pitch on paid seats only. Cash vs DeepSeek official volume at
 * platform ×0.25 — not a second yuan amount, not a feature-list row, not a
 * promise for every model.
 */
export function planValuePitch(plan: SubscriptionPlan): string | null {
  if (plan.priceYuan <= 0) return null;
  return "DeepSeek 约官网 4 倍用量";
}

/** Comparison-card rows. Numbers come from the SKU so the card cannot drift. */
export function planFeatureLines(plan: SubscriptionPlan): string[] {
  return [
    `本月上限 ${plan.monthlyQuotaYuan} 积分`,
    `每日上限 ${plan.dailyQuotaYuan} 积分`,
    `搜索 日 ${plan.searchDaily} · 月 ${plan.searchMonthly}`,
    "全部平台模型",
    "云端、本地、组团、辩论",
  ];
}

const NANO_PER_CNY = 1_000_000_000;

/**
 * Whether the published cost caps still sit below 旗舰.
 * 用量页「将尽」CTA 看 ``seat.plan``；这里留给按帽推断的调用方。
 */
export function publishedSeatCanUpgrade(opts: {
  monthlyCostNano: number;
  dailyCostNano: number;
  dailyHero: boolean;
}): boolean {
  const flagship = SUBSCRIPTION_PLANS.flagship;
  if (opts.dailyHero) {
    if (opts.dailyCostNano <= 0) return false;
    return opts.dailyCostNano < flagship.dailyQuotaYuan * NANO_PER_CNY;
  }
  if (opts.monthlyCostNano <= 0) return false;
  return opts.monthlyCostNano < flagship.monthlyQuotaYuan * NANO_PER_CNY;
}

/** Calendar day of a seat expiry in the viewer's local zone. */
export function formatSeatUntil(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

/**
 * Label the published seat only when both cost caps match a SKU exactly.
 * Unlimited (0) or custom admin numbers → no label. Search caps live on the
 * usage snapshot but are not part of this match.
 */
export function matchPublishedSeat(opts: {
  monthlyCostNano: number;
  dailyCostNano: number;
}): SubscriptionPlanId | null {
  if (opts.monthlyCostNano <= 0 || opts.dailyCostNano <= 0) return null;
  for (const id of SUBSCRIPTION_PLAN_ORDER) {
    const plan = SUBSCRIPTION_PLANS[id];
    if (
      opts.monthlyCostNano === plan.monthlyQuotaYuan * NANO_PER_CNY &&
      opts.dailyCostNano === plan.dailyQuotaYuan * NANO_PER_CNY
    ) {
      return id;
    }
  }
  return null;
}
