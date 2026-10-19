// @vitest-environment jsdom
/**
 * 用量额度行答「还剩」；将尽走 primary；日帽仅更紧时占据额度行。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  type UsageSummary,
  getUsageActivity,
  getUsageSummary,
} from "@/services/usage";
import { useUsageStore } from "@/stores/usage";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

vi.mock("@/services/usage", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/usage")>()),
  getUsageSummary: vi.fn(),
  getUsageActivity: vi.fn(),
}));

import { UsageSettings } from "../UsageSettings";

const mockGet = vi.mocked(getUsageSummary);
const mockActivity = vi.mocked(getUsageActivity);

const EMPTY_ACTIVITY = {
  data: [],
  total: 0,
  usage: {
    input: 0,
    output: 0,
    reasoning: 0,
    cache_hit: 0,
    cache_miss: 0,
  },
  cost: {
    input: 0,
    cached: 0,
    output: 0,
    total: 0,
    currency: "CNY" as const,
    cny_total: 0,
    pricing_source: "curated" as const,
  },
  estimated_cost: null,
  page: 1,
  page_size: 20,
  window: "month" as const,
  model: null,
};

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
    seatPlan?: UsageSummary["seat"]["plan"];
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
    month_by_model: [],
    search: {
      daily_used: 0,
      daily_limit: 40,
      monthly_used: 0,
      monthly_limit: 400,
    },
    quota: {
      daily_tokens: over.dayTokenLimit ?? 1_000_000,
      monthly_cost_nano: monthLimit,
      daily_cost_nano: over.dayCostLimit ?? 0,
    },
    billing_mode: over.billingMode ?? "platform",
    seat: {
      plan: over.seatPlan ?? "basic",
      until: null,
      pending_plan: null,
      store_url: "",
    },
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
  mockActivity.mockReset();
  mockActivity.mockResolvedValue(EMPTY_ACTIVITY);
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

    const hint = await screen.findByText(/接近本月积分/);
    expect(hint.className).toContain("text-primary");
    expect(
      screen.getByRole("link", { name: "去订阅" }).getAttribute("href"),
    ).toBe("/more/subscription");
    expect(hint.className).not.toContain("destructive");
    expect(screen.getByText("2.00 积分")).toBeTruthy();

    const pct = screen.getByText("80%");
    expect(pct.className).toContain("text-primary");
    expect(pct.className).not.toContain("destructive");

    const fill = screen.getByRole("meter", {
      name: "本月积分还剩",
    }).firstElementChild;
    expect(fill?.className).toContain("bg-primary");
    expect(fill?.className).not.toContain("destructive");
  });

  it("flagship near monthly omits 开通更高档", async () => {
    mockGet.mockResolvedValue(
      makeSummary({
        monthCost: 80_000_000_000,
        monthLimit: 100_000_000_000,
        seatPlan: "flagship",
      }),
    );
    renderPage();

    const hint = await screen.findByText(/接近本月积分/);
    expect(hint.textContent).not.toContain("开通更高档");
    expect(screen.queryByRole("link", { name: "去订阅" })).toBeNull();
    expect(
      screen.getByRole("link", { name: "接入自己的 Key" }).getAttribute("href"),
    ).toBe("/more/providers");
  });

  it("below the near threshold stays muted and hides 将尽 copy", async () => {
    mockGet.mockResolvedValue(
      makeSummary({ monthCost: 1_000_000_000, monthLimit: 10_000_000_000 }),
    );
    renderPage();

    await waitFor(() => expect(screen.getByText("本月积分还剩")).toBeTruthy());
    expect(screen.queryByText(/接近本月积分/)).toBeNull();
    expect(screen.getByText("9.00 积分")).toBeTruthy();

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

describe("UsageSettings 额度行与分层", () => {
  it("collapses unlimited quota to a settings row without a meter", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary({
        monthCost: 30_000_000,
        monthLimit: 0,
        dayCostLimit: 0,
      }),
      search: {
        daily_used: 0,
        daily_limit: 0,
        monthly_used: 0,
        monthly_limit: 0,
      },
    });
    renderPage();

    await screen.findByText("本月已用");
    expect(screen.getByText("0.03 积分 · 不限")).toBeTruthy();
    expect(screen.queryByRole("meter")).toBeNull();
    expect(screen.queryByText("本月积分还剩")).toBeNull();
    expect(screen.getByRole("link", { name: "当前席位" })).toBeTruthy();
    expect(screen.getByText("免费版")).toBeTruthy();
  });

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

    await screen.findByText("今日积分还剩");
    expect(screen.getByText("2.00 积分")).toBeTruthy();
    expect(screen.getByText(/本月还剩 10.00 积分/)).toBeTruthy();
    expect(screen.queryByText("本月积分还剩")).toBeNull();
    expect(screen.getByText(/接近今日积分/)).toBeTruthy();
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

    await screen.findByText("本月积分还剩");
    expect(screen.getByText("49.64 积分")).toBeTruthy();
    expect(screen.queryByText("今日积分还剩")).toBeNull();
    expect(screen.queryByRole("meter", { name: "今日积分还剩" })).toBeNull();
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

    await screen.findByText("本月积分还剩");
    expect(screen.queryByRole("heading", { name: "今日" })).toBeNull();
    expect(screen.queryByText("不限")).toBeNull();
    expect(screen.queryByRole("meter", { name: "今日 tokens" })).toBeNull();
    expect(screen.queryByText("今日成本")).toBeNull();
    expect(screen.queryByText("本月成本")).toBeNull();
    expect(screen.queryByText("请求数")).toBeNull();
    expect(screen.queryByText("花费")).toBeNull();
    expect(screen.getByText(/搜索 0 \/ 40/)).toBeTruthy();
    expect(screen.queryByText("输入 / 输出")).toBeNull();
  });

  it("shows cache hit rate on the activity summary when there was a hit", async () => {
    mockGet.mockResolvedValue(makeSummary());
    mockActivity.mockResolvedValue({
      ...EMPTY_ACTIVITY,
      total: 0,
      usage: usageBd(50, 10, 20, 30),
      cost: costBd(2_000_000_000),
    });
    renderPage();

    await screen.findByText(/2.00 积分/);
    expect(screen.getByText(/↓ 50 ↑ 10 · 命中 40%/)).toBeTruthy();
    expect(screen.queryByText(/条/)).toBeNull();
    expect(screen.queryByText(/↓ 100 ↑ 0/)).toBeNull();
    expect(screen.queryByText("今日缓存命中率")).toBeNull();
    expect(screen.queryByRole("heading", { name: "今日" })).toBeNull();
  });

  it("puts the token-near meter on the remaining card", async () => {
    mockGet.mockResolvedValue(
      makeSummary({ dayTokens: 800_000, dayTokenLimit: 1_000_000 }),
    );
    renderPage();

    await screen.findByText("本月积分还剩");
    expect(screen.getByRole("meter", { name: "今日 tokens" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "今日" })).toBeNull();
  });

  it("shows the published seat on the remaining card", async () => {
    mockGet.mockResolvedValue(makeSummary());
    renderPage();

    await screen.findByText("本月积分还剩");
    expect(
      screen.getByRole("link", { name: "当前席位" }).getAttribute("href"),
    ).toBe("/more/subscription");
    expect(screen.getByText("免费版")).toBeTruthy();
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

describe("UsageSettings 近 30 日趋势", () => {
  it("draws a line, restates every day including zeros, and skips a bar stub", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary(),
      recent_daily_cost: TREND_WEEK,
    });
    renderPage();

    await screen.findByText("近 30 日成本");
    expect(screen.getByText("合计 5.80 积分")).toBeTruthy();
    expect(screen.getByTestId("cost-trend-line")).toBeTruthy();
    expect(screen.getAllByTestId("cost-trend-dot")).toHaveLength(7);
    expect(screen.queryByTestId("cost-bar")).toBeNull();

    const table = screen.getByRole("table", { name: "近 30 日每日成本" });
    expect(within(table).getByText("2026-09-10")).toBeTruthy();
    expect(within(table).getByText("2026-09-12")).toBeTruthy();
    expect(within(table).getByText("2.00 积分")).toBeTruthy();
    expect(within(table).getAllByText("—")).toHaveLength(2);
  });

  it("hides the trend when every day is zero", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary(),
      recent_daily_cost: TREND_WEEK.map((p) => ({ ...p, cost_total: 0 })),
    });
    renderPage();

    await screen.findByText("本月积分还剩");
    expect(screen.queryByText("近 30 日成本")).toBeNull();
    expect(screen.queryByTestId("cost-trend-line")).toBeNull();
  });

  it("hides the billed trend in BYOK even if daily points are non-zero", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary({ billingMode: "byok" }),
      recent_daily_cost: TREND_WEEK,
    });
    renderPage();

    await screen.findByText(/当前为「自带 Key」模式/);
    expect(screen.queryByText("近 30 日成本")).toBeNull();
  });
});

describe("UsageSettings 按模型与活动", () => {
  beforeAll(() => {
    globalThis.ResizeObserver ??= class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
    Element.prototype.scrollIntoView ??= () => {};
    Element.prototype.hasPointerCapture ??= () => false;
    Element.prototype.setPointerCapture ??= () => {};
    Element.prototype.releasePointerCapture ??= () => {};
  });

  it("lists this month without a window switch", async () => {
    mockGet.mockResolvedValue(makeSummary());
    renderPage();

    await waitFor(() => {
      expect(mockActivity).toHaveBeenCalledWith(
        expect.objectContaining({ window: "month", page: 1 }),
      );
    });
    expect(screen.queryByRole("tab", { name: "今" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "7 日" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "本月" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "今日" })).toBeNull();
  });

  it("filters activity from the model column", async () => {
    mockGet.mockResolvedValue({
      ...makeSummary(),
      month_by_model: [
        {
          model: "deepseek-v4-pro",
          calls: 3,
          tokens_total: 12000,
          cost_total: 2_400_000_000,
          cost_estimated_total: 0,
        },
      ],
    });
    renderPage();

    const trigger = await screen.findByRole("button", { name: "按模型筛选" });
    expect(screen.queryByText("本月按模型")).toBeNull();
    await waitFor(() => expect(mockActivity).toHaveBeenCalled());
    mockActivity.mockClear();

    fireEvent.pointerDown(trigger);
    fireEvent.click(screen.getByRole("menuitem", { name: /deepseek-v4-pro/ }));
    await waitFor(() => {
      expect(mockActivity).toHaveBeenCalledWith(
        expect.objectContaining({
          model: "deepseek-v4-pro",
          page: 1,
          window: "month",
        }),
      );
    });

    mockActivity.mockClear();
    fireEvent.pointerDown(screen.getByRole("button", { name: "按模型筛选" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /全部/ }));
    await waitFor(() => {
      expect(mockActivity).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, window: "month" }),
      );
    });
    expect(mockActivity.mock.calls.at(-1)?.[0].model).toBeUndefined();
  });

  it("opens a conversation from the activity list", async () => {
    mockGet.mockResolvedValue(makeSummary());
    mockActivity.mockResolvedValue({
      ...EMPTY_ACTIVITY,
      total: 1,
      data: [
        {
          conversation_id: "conv-1",
          kind: "conversation",
          title: "调研竞品",
          deleted: false,
          models: ["deepseek-v4-pro"],
          usage: usageBd(6184, 4716, 2976, 3208),
          cost: costBd(15_000_000),
          estimated_cost: null,
          last_at: "2026-10-19T03:04:00Z",
          turns: 1,
        },
      ],
    });
    renderPage();

    const row = await screen.findByRole("link", { name: /调研竞品/ });
    expect(row.getAttribute("href")).toBe("/conversations/conv-1");
    expect(screen.getByText("最近活动")).toBeTruthy();
    const table = screen.getByRole("table", { name: "最近活动" });
    expect(
      within(table).getByRole("columnheader", { name: "对话" }),
    ).toBeTruthy();
    expect(
      within(table).getByRole("columnheader", { name: "模型" }),
    ).toBeTruthy();
    expect(
      within(table).getByRole("columnheader", { name: "TOKEN" }),
    ).toBeTruthy();
    expect(
      within(table).getByRole("columnheader", { name: "费用" }),
    ).toBeTruthy();
    expect(
      within(table).getByRole("columnheader", { name: "时间" }),
    ).toBeTruthy();
    expect(within(table).getByText("deepseek-v4-pro")).toBeTruthy();
    expect(within(table).getByText("↓ 6.2k ↑ 4.7k · 命中 48%")).toBeTruthy();
    expect(screen.getByText("第 1–1 条 · 共 1 条")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "下一页" })).toBeNull();
  });

  it("does not link account-level AI 改写", async () => {
    mockGet.mockResolvedValue(makeSummary());
    mockActivity.mockResolvedValue({
      ...EMPTY_ACTIVITY,
      total: 1,
      data: [
        {
          conversation_id: null,
          kind: "assist",
          title: "AI 改写",
          deleted: false,
          models: ["deepseek-v4-flash"],
          usage: usageBd(20, 10),
          cost: costBd(1_000_000),
          estimated_cost: null,
          last_at: "2026-10-19T03:04:00Z",
          turns: 0,
        },
      ],
    });
    renderPage();

    await screen.findByText("AI 改写");
    expect(screen.queryByRole("link", { name: /AI 改写/ })).toBeNull();
  });

  it("replaces the activity page instead of appending", async () => {
    mockGet.mockResolvedValue(makeSummary());
    mockActivity.mockResolvedValue({
      ...EMPTY_ACTIVITY,
      total: 21,
      page: 1,
      page_size: 20,
      data: [
        {
          conversation_id: "conv-1",
          kind: "conversation",
          title: "第一页",
          deleted: false,
          models: ["deepseek-v4-pro"],
          usage: usageBd(10, 4),
          cost: costBd(15_000_000),
          estimated_cost: null,
          last_at: "2026-10-19T03:04:00Z",
          turns: 1,
        },
      ],
    });
    renderPage();

    await screen.findByText("第一页");
    expect(screen.getByText("第 1–20 条 · 共 21 条")).toBeTruthy();
    expect(screen.getByText("1 / 2")).toBeTruthy();
    expect(screen.queryByText("加载更多")).toBeNull();
    expect(
      (screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    mockActivity.mockResolvedValue({
      ...EMPTY_ACTIVITY,
      total: 21,
      page: 2,
      page_size: 20,
      data: [
        {
          conversation_id: "conv-2",
          kind: "conversation",
          title: "第二页",
          deleted: false,
          models: ["deepseek-v4-flash"],
          usage: usageBd(8, 3),
          cost: costBd(9_000_000),
          estimated_cost: null,
          last_at: "2026-10-18T03:04:00Z",
          turns: 1,
        },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    await screen.findByText("第二页");
    expect(screen.queryByText("第一页")).toBeNull();
    expect(mockActivity).toHaveBeenCalledWith(
      expect.objectContaining({ window: "month", page: 2 }),
    );
    expect(screen.getByText("第 21–21 条 · 共 21 条")).toBeTruthy();
    expect(screen.getByText("2 / 2")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});
