// @vitest-environment jsdom
/**
 * 用量主卡答「还剩」；将尽走 primary；日帽仅更紧时占据主卡。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { type UsageSummary, getUsageSummary } from "@/services/usage";
import { useUsageStore } from "@/stores/usage";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/usage", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/usage")>()),
  getUsageSummary: vi.fn(),
}));

import { UsageSettings } from "../UsageSettings";

const mockGet = vi.mocked(getUsageSummary);

function usageBd(input = 0, output = 0, cacheHit = 0, cacheMiss = 0) {
  return {
    input,
    output,
    reasoning: 0,
    cache_hit: cacheHit,
    cache_miss: cacheMiss,
  };
}

function costBd(total = 0) {
  return {
    input: 0,
    cached: 0,
    output: 0,
    total,
    currency: "CNY" as const,
    cny_total: 0,
    pricing_source: "curated" as const,
  };
}

function makeSummary(
  over: {
    monthCost?: number;
    monthLimit?: number;
    dayCost?: number;
    dayCostLimit?: number;
    dayTokens?: number;
    dayTokenLimit?: number;
    dayRequests?: number;
    cacheHit?: number;
    cacheMiss?: number;
    billingMode?: UsageSummary["billing_mode"];
  } = {},
): UsageSummary {
  const monthCost = over.monthCost ?? 1_000_000_000;
  const monthLimit = over.monthLimit ?? 10_000_000_000;
  const input = over.dayTokens ?? 100;
  return {
    today: {
      usage: usageBd(input, 0, over.cacheHit ?? 0, over.cacheMiss ?? 0),
      cost: costBd(over.dayCost ?? 0),
      requests: over.dayRequests ?? 1,
    },
    month: {
      usage: usageBd(),
      cost: costBd(monthCost),
      requests: 1,
    },
    recent_daily_cost: [],
    quota: {
      daily_tokens: over.dayTokenLimit ?? 1_000_000,
      monthly_cost_nano: monthLimit,
      daily_cost_nano: over.dayCostLimit ?? 0,
    },
    billing_mode: over.billingMode ?? "platform",
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <UsageSettings />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockGet.mockReset();
  useUsageStore.setState({
    summary: null,
    loading: false,
    error: null,
    messageCosts: {},
  });
});

afterEach(() => {
  cleanup();
});

describe("UsageSettings 将尽 tone", () => {
  it("near copy and meter use primary, not destructive", async () => {
    mockGet.mockResolvedValue(
      makeSummary({ monthCost: 8_000_000_000, monthLimit: 10_000_000_000 }),
    );
    renderPage();

    const hint = await screen.findByText(/接近本月额度/);
    expect(hint.className).toContain("text-primary");
    expect(hint.className).not.toContain("destructive");
    expect(screen.getByText("¥2.00")).toBeTruthy();

    const pct = screen.getByText("80%");
    expect(pct.className).toContain("text-primary");
    expect(pct.className).not.toContain("destructive");

    const fill = screen.getByRole("meter", {
      name: "本月额度还剩",
    }).firstElementChild;
    expect(fill?.className).toContain("bg-primary");
    expect(fill?.className).not.toContain("destructive");
  });

  it("below the near threshold stays muted and hides 将尽 copy", async () => {
    mockGet.mockResolvedValue(
      makeSummary({ monthCost: 1_000_000_000, monthLimit: 10_000_000_000 }),
    );
    renderPage();

    await waitFor(() => expect(screen.getByText("本月额度还剩")).toBeTruthy());
    expect(screen.queryByText(/接近本月额度/)).toBeNull();
    expect(screen.getByText("¥9.00")).toBeTruthy();

    const pct = screen.getByText("10%");
    expect(pct.className).toContain("text-muted-foreground");
    expect(pct.className).not.toContain("text-primary");
    expect(pct.className).not.toContain("destructive");
  });

  it("refresh failure banner stays muted, not destructive", async () => {
    useUsageStore.setState({
      summary: makeSummary(),
      loading: false,
      error: null,
      messageCosts: {},
    });
    mockGet.mockRejectedValue(new Error("offline"));
    renderPage();

    const msg = await screen.findByText("用量加载失败，请重试");
    expect(msg.className).toContain("text-muted-foreground");
    expect(msg.className).not.toContain("destructive");
    expect(msg.parentElement?.className).toContain("bg-muted/40");
    expect(msg.parentElement?.className).not.toContain("destructive");
  });
});

describe("UsageSettings 主卡与分层", () => {
  it("uses the daily cap as the hero when it is the tighter remainder", async () => {
    mockGet.mockResolvedValue(
      makeSummary({
        monthCost: 40_000_000_000,
        monthLimit: 50_000_000_000,
        dayCost: 8_000_000_000,
        dayCostLimit: 10_000_000_000,
      }),
    );
    renderPage();

    await screen.findByText("今日额度还剩");
    expect(screen.getByText("¥2.00")).toBeTruthy();
    expect(screen.getByText(/本月还剩 ¥10.00/)).toBeTruthy();
    expect(screen.queryByText("本月额度还剩")).toBeNull();
    expect(screen.getByText(/接近今日额度/)).toBeTruthy();
  });

  it("keeps the monthly hero when daily remaining is not tighter", async () => {
    mockGet.mockResolvedValue(
      makeSummary({
        monthCost: 360_000_000,
        monthLimit: 50_000_000_000,
        dayCost: 10_000_000,
        dayCostLimit: 50_000_000_000,
      }),
    );
    renderPage();

    await screen.findByText("本月额度还剩");
    expect(screen.getByText("¥49.64")).toBeTruthy();
    expect(screen.queryByText("今日额度还剩")).toBeNull();
    expect(screen.queryByRole("meter", { name: "今日额度还剩" })).toBeNull();
  });

  it("does not draw an unlimited token meter or repeat cost rows", async () => {
    mockGet.mockResolvedValue(
      makeSummary({
        dayTokenLimit: 0,
        cacheHit: 0,
        cacheMiss: 50,
      }),
    );
    renderPage();

    await screen.findByText("本月额度还剩");
    expect(screen.queryByText("不限")).toBeNull();
    expect(screen.queryByRole("meter", { name: "今日 tokens" })).toBeNull();
    expect(screen.queryByText("今日成本")).toBeNull();
    expect(screen.queryByText("本月成本")).toBeNull();
    expect(screen.queryByText("请求数")).toBeNull();
    expect(screen.queryByText(/命中率/)).toBeNull();
    expect(screen.getByText("花费")).toBeTruthy();
    expect(screen.getByText("tokens")).toBeTruthy();
    expect(screen.getByText("输入 / 输出")).toBeTruthy();
  });

  it("shows cache hit rate only when there was a hit", async () => {
    mockGet.mockResolvedValue(
      makeSummary({ dayTokens: 100, cacheHit: 20, cacheMiss: 80 }),
    );
    renderPage();

    await screen.findByText("今日缓存命中率");
    expect(screen.getByText("20%")).toBeTruthy();
  });
});

const TREND_WEEK: UsageSummary["recent_daily_cost"] = [
  { date: "2026-09-10", cost_total: 0 },
  { date: "2026-09-11", cost_total: 1_000_000_000 },
  { date: "2026-09-12", cost_total: 2_000_000_000 },
  { date: "2026-09-13", cost_total: 0 },
  { date: "2026-09-14", cost_total: 500_000_000 },
  { date: "2026-09-15", cost_total: 1_500_000_000 },
  { date: "2026-09-16", cost_total: 800_000_000 },
];

describe("UsageSettings 近 7 日趋势", () => {
  it("draws a line, restates every day including zeros, and skips a bar stub", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary(),
      recent_daily_cost: TREND_WEEK,
    });
    renderPage();

    await screen.findByText("近 7 日成本");
    expect(screen.getByText("合计 ¥5.80")).toBeTruthy();
    expect(screen.getByTestId("cost-trend-line")).toBeTruthy();
    expect(screen.getAllByTestId("cost-trend-dot")).toHaveLength(7);
    expect(screen.queryByTestId("cost-bar")).toBeNull();

    const table = screen.getByRole("table", { name: "近 7 日每日成本" });
    expect(within(table).getByText("2026-09-10")).toBeTruthy();
    expect(within(table).getByText("2026-09-12")).toBeTruthy();
    expect(within(table).getByText("¥2.00")).toBeTruthy();
    expect(within(table).getAllByText("—")).toHaveLength(2);
  });

  it("hides the trend when every day is zero", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary(),
      recent_daily_cost: TREND_WEEK.map((p) => ({ ...p, cost_total: 0 })),
    });
    renderPage();

    await screen.findByText("本月额度还剩");
    expect(screen.queryByText("近 7 日成本")).toBeNull();
    expect(screen.queryByTestId("cost-trend-line")).toBeNull();
  });

  it("hides the billed trend in BYOK even if daily points are non-zero", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary({ billingMode: "byok" }),
      recent_daily_cost: TREND_WEEK,
    });
    renderPage();

    await screen.findByText(/当前为「自带 Key」模式/);
    expect(screen.queryByText("近 7 日成本")).toBeNull();
  });
});
