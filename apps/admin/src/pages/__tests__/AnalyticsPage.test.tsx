// @vitest-environment jsdom
/**
 * Render tests for the admin 供给·成本 page.
 *
 * AnalyticsPage is the cost room of 供给: fetchUsageSummary + Go windows + 会话复盘
 * drill-in. Health pulse lives on 总览. These pin the cost render, BYOK framing,
 * and replay / user drill-ins with services + trend charts mocked.
 */

import { AnalyticsPage } from "@/pages/AnalyticsPage";
import {
  type AdminGoWindows,
  type AdminUsageSummary,
  type UsageWindow,
  fetchGoWindows,
  fetchUsageSummary,
} from "@/services/adminUsage";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminUsage", () => ({
  fetchUsageSummary: vi.fn(),
  fetchGoWindows: vi.fn(),
}));
// Trend charts are not under test — stub them to keep the test on the page's own layout.
vi.mock("@/components/charts", () => ({
  CostTrendBars: () => <div data-testid="cost-trend" />,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function usageWindow(cnyTotal: number, requests: number): UsageWindow {
  return {
    cost: {
      cached: 0,
      cny_total: cnyTotal,
      currency: "CNY",
      input: 0,
      output: 0,
      total: 0,
      pricing_source: "curated",
    },
    usage: { cache_hit: 0, cache_miss: 0, input: 0, output: 0, reasoning: 0 },
    requests,
  };
}

function goWindows(p?: Partial<AdminGoWindows>): AdminGoWindows {
  return {
    as_of: "2026-08-18T12:00:00Z",
    cost_basis: "nominal_nano_cny",
    estimate_basis: "opencode_public_list",
    estimate_currency: "USD",
    estimate_price_as_of: "2026-08-18",
    estimate_model: "deepseek-v4-flash",
    subscription_day: 15,
    five_hour: {
      cost_total_nano: 2_000_000_000,
      estimated_usd_nano: 1_230_000_000,
      calls: 4,
      started_at: "2026-08-18T10:00:00Z",
      reset_at: "2026-08-18T15:00:00Z",
    },
    weekly: {
      cost_total_nano: 3_000_000_000,
      estimated_usd_nano: 4_500_000_000,
      calls: 8,
      started_at: "2026-08-17T00:00:00Z",
      reset_at: "2026-08-24T00:00:00Z",
    },
    monthly: {
      cost_total_nano: 4_000_000_000,
      estimated_usd_nano: 8_000_000_000,
      calls: 12,
      started_at: "2026-08-15T00:00:00Z",
      reset_at: "2026-09-15T00:00:00Z",
    },
    members: [],
    ...p,
  };
}

function usageSummary(p?: Partial<AdminUsageSummary>): AdminUsageSummary {
  return {
    billing_mode: "platform",
    today: usageWindow(12.5, 3),
    month: usageWindow(88, 9),
    month_by_model: [],
    month_by_user: [
      {
        user_id: "u1",
        username: "alice",
        display_name: "Alice",
        cost_total: 1_000_000_000,
        turns: 5,
      },
    ],
    recent_daily_cost: [],
    ...p,
  };
}

/** Probe route so a navigate("/replay/:id") drill-in is asserted by rendered text. */
function ReplayProbe() {
  const { id } = useParams<{ id: string }>();
  return <div>复盘页 {id}</div>;
}

/** Same idea for the Top-spender drill-in into 用户详情. */
function UserProbe() {
  const { userId } = useParams<{ userId: string }>();
  return <div>用户页 {userId}</div>;
}

function renderAnalytics(initial = "/analytics/cost") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/analytics/cost" element={<AnalyticsPage />} />
        <Route path="/quota" element={<div>额度页</div>} />
        <Route path="/replay/:id" element={<ReplayProbe />} />
        <Route path="/users/:userId" element={<UserProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AnalyticsPage", () => {
  beforeEach(() => {
    vi.mocked(fetchGoWindows).mockResolvedValue(goWindows());
  });

  it("renders the 成本 lens: window totals + top spenders + trend", async () => {
    vi.mocked(fetchUsageSummary).mockResolvedValue(usageSummary());
    renderAnalytics("/analytics/cost");
    // Window labels carry a（UTC）suffix — the backend cuts these windows on UTC days.
    expect(await screen.findByText(/今日总成本/)).toBeTruthy();
    expect(screen.getByText(/本月总成本/)).toBeTruthy();
    expect(screen.getByText("¥12.50")).toBeTruthy(); // today cny_total = 12.5
    expect(screen.getByText("Alice")).toBeTruthy(); // top spender row
    expect(screen.getByTestId("cost-trend")).toBeTruthy();
    expect(fetchUsageSummary).toHaveBeenCalledTimes(1);
    expect(fetchGoWindows).toHaveBeenCalledTimes(1);
    expect(screen.getByText("OpenCode Go 窗口")).toBeTruthy();
    expect(screen.getByText("5 小时窗")).toBeTruthy();
    expect(screen.getByText("本周（UTC 周一）")).toBeTruthy();
    expect(screen.getByText("本月（订阅日 15）")).toBeTruthy();
    expect(screen.getByText(/不是上游美元用量/)).toBeTruthy();
    expect(screen.getByText(/costMultiplier/)).toBeTruthy();
    expect(screen.getByText(/会低估/)).toBeTruthy();
    expect(screen.getByText(/≈\$1\.23/)).toBeTruthy();
    expect(screen.getByText(/\/ \$12/)).toBeTruthy();
    expect(screen.getAllByText(/公开单价估算 · 非上游账单/).length).toBeGreaterThan(0);
  });

  it("shows the BYOK framing when billing_mode is byok", async () => {
    vi.mocked(fetchUsageSummary).mockResolvedValue(
      usageSummary({ billing_mode: "byok" }),
    );
    renderAnalytics("/analytics/cost");
    expect(await screen.findByText(/BYOK/)).toBeTruthy();
  });

  it("offers the 额度 room as a supply tab", async () => {
    vi.mocked(fetchUsageSummary).mockResolvedValue(usageSummary());
    renderAnalytics("/analytics/cost");
    await screen.findByText(/今日总成本/);
    expect(screen.getByRole("link", { name: "额度" }).getAttribute("href")).toBe(
      "/quota",
    );
  });

  it("opens 复盘 from the 会话 ID form", async () => {
    vi.mocked(fetchUsageSummary).mockResolvedValue(usageSummary());
    renderAnalytics("/analytics/cost");
    await screen.findByText(/今日总成本/);
    fireEvent.change(screen.getByPlaceholderText("会话 ID 复盘…"), {
      target: { value: "conv-42" },
    });
    fireEvent.click(screen.getByRole("button", { name: "复盘" }));
    expect(await screen.findByText(/复盘页 conv-42/)).toBeTruthy();
  });

  it("opens 用户详情 from a Top-spender row without a mouse", async () => {
    vi.mocked(fetchUsageSummary).mockResolvedValue(usageSummary());
    renderAnalytics("/analytics/cost");
    // 这些行一直是「看着能点、键盘够不着」——名字来自 TableRow 的 label。
    const row = await screen.findByRole("row", { name: /打开用户详情 Alice/ });
    fireEvent.keyDown(row, { key: "Enter" });
    expect(await screen.findByText(/用户页 u1/)).toBeTruthy();
  });

  it("offers a retry when a lens fails to load", async () => {
    vi.mocked(fetchUsageSummary).mockRejectedValueOnce(new Error("down"));
    renderAnalytics("/analytics/cost");
    expect(await screen.findByText("发生未知错误")).toBeTruthy();

    vi.mocked(fetchUsageSummary).mockResolvedValue(usageSummary());
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText(/今日总成本/)).toBeTruthy();
  });
});
