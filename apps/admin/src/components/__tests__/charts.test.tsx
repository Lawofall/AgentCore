// @vitest-environment jsdom
/**
 * Shared 7-day trend bars (总览 / 供给·成本 / 用户详情 all render these).
 *
 * The drawn bars are decorative: their only value carrier used to be a hover
 * `title`, which no keyboard or screen-reader user can reach. What these pin is the
 * readable half — every plotted number restated as a table, the caller's currency
 * kept as-is, a zero day drawing no bar, and "no buckets at all" landing on an empty
 * state instead of an empty frame. The leading block comment keeps the
 * @vitest-environment directive file-leading.
 */

import { CostTrendBars, TurnTrendBars } from "@/components/charts";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("CostTrendBars", () => {
  it("restates every bar as a readable row in the caller's currency", () => {
    render(
      <CostTrendBars
        data={[
          { date: "2026-06-28", cost_total: 1_500_000_000 },
          { date: "2026-06-29", cost_total: 3_000_000_000 },
        ]}
        currency="USD"
      />,
    );

    const table = screen.getByRole("table", { name: /每日记账成本/ });
    // 币种跟随调用方下发的 breakdown——绝不把美元估算重刷成 ¥。
    expect(within(table).getByText("$1.50")).toBeTruthy();
    expect(within(table).getByText("$3.00")).toBeTruthy();
    expect(screen.getAllByTestId("cost-bar")).toHaveLength(2);
  });

  it("draws no bar for a zero day but still lists it", () => {
    render(
      <CostTrendBars
        data={[
          { date: "2026-06-28", cost_total: 0 },
          { date: "2026-06-29", cost_total: 2_000_000_000 },
        ]}
        currency="CNY"
      />,
    );

    // 空白日给个地板高度的柱子会读成「天天都有花销」——BYOK 下正是误读所在。
    expect(screen.getAllByTestId("cost-bar")).toHaveLength(1);
    const table = screen.getByRole("table", { name: /每日记账成本/ });
    expect(within(table).getByText("¥0.00")).toBeTruthy();
  });

  it("shows an empty state when there are no buckets at all", () => {
    render(<CostTrendBars data={[]} currency="CNY" />);

    expect(screen.getByText("暂无趋势数据")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByTestId("cost-bar")).toBeNull();
  });
});

describe("TurnTrendBars", () => {
  it("restates turns and errors per day", () => {
    render(
      <TurnTrendBars
        data={[
          { date: "2026-06-28", turns: 10, errors: 0 },
          { date: "2026-06-29", turns: 8, errors: 3 },
        ]}
      />,
    );

    const table = screen.getByRole("table", { name: /每日回合与错误数/ });
    expect(within(table).getByText("10 / 0")).toBeTruthy();
    expect(within(table).getByText("8 / 3")).toBeTruthy();
    expect(screen.getAllByTestId("turn-error-bar")).toHaveLength(1);
  });

  it("shows an empty state when there are no buckets at all", () => {
    render(<TurnTrendBars data={[]} />);

    expect(screen.getByText("暂无趋势数据")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
