// @vitest-environment jsdom
/**
 * Render tests for the admin 总览 landing page.
 *
 * 总览 pulls one bundle (`fetchOverview`) and used to drop half of it on the floor:
 * both 7-day trends were fetched and never drawn, and today's token usage had no
 * tile at all. These pin the whole bundle reaching the screen (with the trend
 * numbers readable, not just drawn), the refresh state keeping the previous
 * snapshot, and the keyboard path into 会话复盘. The leading block comment keeps the
 * @vitest-environment directive file-leading.
 */

import { OverviewPage } from "@/pages/OverviewPage";
import type {
  TurnHealthWindow,
  TurnMetricLine,
} from "@/services/adminObservability";
import { type AdminOverview, fetchOverview } from "@/services/adminOverview";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminOverview", () => ({ fetchOverview: vi.fn() }));
vi.mock("@/services/adminSystem", () => ({
  fetchSystemStatus: vi.fn(),
}));
vi.mock("@/services/releaseDrift", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/releaseDrift")>()),
  fetchReleaseDrift: vi.fn(() => Promise.resolve(null)),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function healthWindow(p?: Partial<TurnHealthWindow>): TurnHealthWindow {
  return {
    avg_duration_ms: 900,
    avg_rounds: 1.5,
    delegated_rate: 0.25,
    delegated_turns: 10,
    error_rate: 0.05,
    errors: 2,
    escalations: 0,
    first_plan_survival_rate: 0.8,
    input_tokens: 120_000,
    output_tokens: 30_000,
    p95_duration_ms: 2400,
    revises: 0,
    scope_signals: 0,
    turns: 40,
    ...p,
  };
}

function errLine(
  p: Partial<TurnMetricLine> & { turn_id: string; conversation_id: string },
): TurnMetricLine {
  return {
    agent_id: null,
    created_at: "2026-06-30T10:00:00Z",
    delegated: false,
    duration_ms: 1200,
    error: "boom",
    finish_reason: "error",
    input_tokens: 0,
    kind: "chat",
    output_tokens: 0,
    rounds: 1,
    status: "error",
    trace_id: "trace123",
    user_id: "u1",
    workers: 0,
    ...p,
  };
}

function overview(p?: Partial<AdminOverview>): AdminOverview {
  return {
    active_users_today: 3,
    admins: 1,
    billing_mode: "platform",
    cost_today: {
      cached: 0,
      cny_total: 12.5,
      currency: "CNY",
      input: 0,
      output: 0,
      total: 0,
      pricing_source: "curated",
    },
    database_ok: true,
    recent_daily_cost: [{ date: "2026-06-29", cost_total: 2_000_000_000 }],
    recent_daily_turns: [{ date: "2026-06-29", turns: 40, errors: 2 }],
    recent_errors: [],
    today: healthWindow(),
    users_active: 9,
    users_total: 12,
    ...p,
  };
}

/** Probe route so a navigate("/replay/:id") drill-in is asserted by rendered text. */
function ReplayProbe() {
  const { id } = useParams<{ id: string }>();
  return <div>复盘页 {id}</div>;
}

function renderOverview() {
  return render(
    <MemoryRouter initialEntries={["/overview"]}>
      <Routes>
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/replay/:id" element={<ReplayProbe />} />
        <Route path="/quota" element={<div>供给额度页</div>} />
        <Route path="/conversations" element={<div>对话页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("OverviewPage", () => {
  it("renders today's pulse including the token tile", async () => {
    vi.mocked(fetchOverview).mockResolvedValue(overview());
    renderOverview();

    expect(await screen.findByText("今日活跃用户")).toBeTruthy();
    expect(screen.getByText("¥12.50")).toBeTruthy();
    // 今日 token = 该回合窗口的输入 + 输出，两者也分别列出。
    expect(screen.getByText("今日 Token")).toBeTruthy();
    expect(screen.getByText("15万")).toBeTruthy();
    expect(screen.getByText("输入 12万 · 输出 3万")).toBeTruthy();
  });

  it("draws the 7-day turn trend it fetches, with the numbers readable", async () => {
    vi.mocked(fetchOverview).mockResolvedValue(overview());
    renderOverview();

    await screen.findByText("今日活跃用户");
    const turnTable = screen.getByRole("table", { name: /每日回合与错误数/ });
    expect(within(turnTable).getByText("40 / 2")).toBeTruthy();
    expect(screen.getAllByTestId("turn-bar")).toHaveLength(1);
    expect(screen.queryByRole("table", { name: /每日记账成本/ })).toBeNull();
  });

  it("keeps the previous snapshot on screen while refreshing", async () => {
    vi.mocked(fetchOverview).mockResolvedValue(overview());
    renderOverview();
    await screen.findByText("今日活跃用户");

    let resolve!: (v: AdminOverview) => void;
    vi.mocked(fetchOverview).mockReturnValue(
      new Promise<AdminOverview>((r) => {
        resolve = r;
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));

    // 刷新不得把页面塌成 spinner：旧快照留在原处，只是被标记为忙。
    await waitFor(() =>
      expect(document.querySelector('[aria-busy="true"]')).toBeTruthy(),
    );
    expect(screen.getByText("¥12.50")).toBeTruthy();

    resolve(overview({ cost_today: { ...overview().cost_today, cny_total: 20 } }));
    expect(await screen.findByText("¥20.00")).toBeTruthy();
    expect(document.querySelector('[aria-busy="true"]')).toBeNull();
  });

  it("drills into 会话复盘 from an error row by keyboard", async () => {
    vi.mocked(fetchOverview).mockResolvedValue(
      overview({
        recent_errors: [errLine({ turn_id: "t1", conversation_id: "conv-9" })],
      }),
    );
    renderOverview();

    const row = await screen.findByRole("row", { name: /打开会话复盘 conv-9/ });
    fireEvent.keyDown(row, { key: "Enter" });
    expect(await screen.findByText(/复盘页 conv-9/)).toBeTruthy();
  });

  it("states an empty error feed rather than showing an empty table", async () => {
    vi.mocked(fetchOverview).mockResolvedValue(overview({ recent_errors: [] }));
    renderOverview();

    expect(await screen.findByText("近期暂无错误回合")).toBeTruthy();
  });

  it("offers a retry when the first load fails", async () => {
    vi.mocked(fetchOverview).mockRejectedValueOnce(new Error("down"));
    renderOverview();

    expect(await screen.findByText("发生未知错误")).toBeTruthy();
    expect(screen.queryByText("今日活跃用户")).toBeNull();

    vi.mocked(fetchOverview).mockResolvedValue(overview());
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("今日活跃用户")).toBeTruthy();
  });

  it("sends 计费模式 to 供给·额度", async () => {
    vi.mocked(fetchOverview).mockResolvedValue(overview());
    renderOverview();
    await screen.findByText("今日活跃用户");

    fireEvent.click(screen.getByRole("button", { name: /计费模式/ }));
    expect(await screen.findByText("供给额度页")).toBeTruthy();
  });
});
