// @vitest-environment jsdom
/**
 * Layout pins for 用户详情.
 *
 * This page kept four hand-rolled `<table>`s inside `overflow-hidden` cards — the one
 * shape in this console that clips instead of scrolling, and the page the skeleton-layer
 * pass missed. At 375 the 登录会话 columns (User-Agent / 平台 / 最近使用 / 创建时间) ran
 * past the right edge with no scrollbar and no way to swipe to them, so the data was
 * simply unreachable. These pin that every table now sits in `TableFrame`, and that the
 * two drill-in tables kept their row activation across the swap. The leading block
 * comment keeps the @vitest-environment directive file-leading.
 */

import { UserDetail } from "@/components/UserDetail";
import {
  type AdminUserDetail,
  type SessionSummary,
  fetchUserDetail,
} from "@/services/adminUsers";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminUsers", () => ({
  fetchUserDetail: vi.fn(),
  // Pulled in by the password dialogs this page mounts on demand.
  resetUserPassword: vi.fn(),
  setUserPassword: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// Not under test — the trend chart has nothing to measure in jsdom.
vi.mock("@/components/charts", () => ({
  CostTrendBars: () => <div data-testid="cost-trend" />,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const WINDOW: AdminUserDetail["today"] = {
  cost: {
    cached: 0,
    cny_total: 1.5,
    currency: "CNY",
    input: 0,
    output: 0,
    pricing_source: "curated",
    total: 0,
  },
  usage: { cache_hit: 0, cache_miss: 0, input: 100, output: 200, reasoning: 0 },
  requests: 3,
};

function session(p: Partial<SessionSummary> & { id: string }): SessionSummary {
  return {
    created_at: "2026-08-01T02:00:00Z",
    current: false,
    ip: "203.0.113.9",
    last_used_at: "2026-08-13T09:30:00Z",
    platform: "windows",
    user_agent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) AgentCore/0.3.1 Chrome/126 Electron/31 Safari/537.36",
    ...p,
  };
}

function detail(p?: Partial<AdminUserDetail>): AdminUserDetail {
  return {
    background_model: null,
    billing_mode: "platform",
    conversations: [
      {
        created_at: "2026-08-10T00:00:00Z",
        id: "c1",
        messages: 6,
        title: "排障会话",
        updated_at: "2026-08-13T08:00:00Z",
      },
    ],
    default_model: "anthropic/claude-sonnet-4-5",
    month: WINDOW,
    provider_count: 1,
    recent_by_model: [
      {
        calls: 3,
        cost_estimated_total: 0,
        cost_total: 1_000_000_000,
        model: "anthropic/claude-sonnet-4-5-20250929-thinking-max",
        tokens_total: 4200,
      },
    ],
    recent_daily_cost: [],
    recent_turns: [
      {
        agent_id: null,
        conversation_id: "c1",
        created_at: "2026-08-13T08:00:00Z",
        delegated: false,
        duration_ms: 1200,
        error: null,
        finish_reason: "stop",
        input_tokens: 10,
        kind: "chat",
        output_tokens: 20,
        rounds: 1,
        status: "ok",
        trace_id: null,
        turn_id: "t1",
        user_id: "u1",
        workers: 0,
      },
    ],
    sessions: [session({ id: "s1" })],
    today: WINDOW,
    user: {
      created_at: "2026-06-01T00:00:00Z",
      deleted_at: null,
      display_name: "Alice",
      email: null,
      id: "u1",
      is_unlimited: false,
      quota_daily_cost_cny: null,
      quota_daily_tokens: null,
      quota_monthly_cost_cny: null,
      registration_ip: "203.0.113.9",
      role: "user",
      status: "active",
      username: "alice",
    },
    ...p,
  };
}

/** Probe route so a navigate("/replay/:id") drill-in is asserted by rendered text. */
function ReplayProbe() {
  const { id } = useParams<{ id: string }>();
  return <div>复盘页 {id}</div>;
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/users/u1"]}>
      <Routes>
        <Route
          path="/users/:userId"
          element={<UserDetail userId="u1" onBack={() => {}} />}
        />
        <Route path="/replay/:id" element={<ReplayProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("UserDetail", () => {
  it("四张表都在可横向滚动的表框里，右侧列不再被切掉", async () => {
    vi.mocked(fetchUserDetail).mockResolvedValue(detail());
    const { container } = renderDetail();
    await screen.findByText("登录会话");

    const tables = [...container.querySelectorAll("table")];
    expect(tables.length).toBe(4);
    for (const table of tables) {
      // The scroll container is the frame wrapping the table; the card above it stays
      // `overflow-hidden` for its corners, which is exactly what made the overflowing
      // columns unreachable while the table sat directly inside it.
      expect(table.parentElement?.className).toContain("overflow-x-auto");
      expect(table.style.minWidth).not.toBe("");
    }

    // The columns that used to sit past the right edge with no way to reach them.
    expect(screen.getByText("最近使用")).toBeTruthy();
    expect(screen.getByText("创建时间")).toBeTruthy();
  });

  it("最近会话行仍能点进会话复盘", async () => {
    vi.mocked(fetchUserDetail).mockResolvedValue(detail());
    renderDetail();

    fireEvent.click(await screen.findByText("排障会话"));
    expect(await screen.findByText(/复盘页 c1/)).toBeTruthy();
  });

  it("最近活动行可用键盘进会话复盘", async () => {
    vi.mocked(fetchUserDetail).mockResolvedValue(detail());
    renderDetail();

    const row = await screen.findByRole("row", { name: /打开会话复盘 c1/ });
    fireEvent.keyDown(row, { key: "Enter" });
    expect(await screen.findByText(/复盘页 c1/)).toBeTruthy();
  });
});
