import {
  type PaidPlanId,
  SEAT_STORE_URL,
  type SubscriptionPlanId,
  formatSeatUntil,
} from "@/lib/subscriptionPlans";
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import {
  SUBSCRIPTION_ORDER_QQ,
  SubscriptionSettings,
} from "../SubscriptionSettings";

function renderPage(
  opts: {
    currentPlanId?: SubscriptionPlanId | null;
    storeUrl?: string;
    seatUntil?: string | null;
    pendingPlanId?: PaidPlanId | null;
  } = {},
) {
  return render(
    <MemoryRouter>
      <SubscriptionSettings
        currentPlanId={opts.currentPlanId ?? null}
        storeUrl={opts.storeUrl}
        seatUntil={opts.seatUntil}
        pendingPlanId={opts.pendingPlanId}
      />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("SubscriptionSettings", () => {
  it("lists three paid seats in a row and the redeem field, without a payment QR", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "订阅" })).toBeTruthy();
    expect(screen.queryByText("免费版")).toBeNull();
    expect(screen.queryByText("覆盖日常试用")).toBeNull();
    expect(screen.queryByText("免费")).toBeNull();
    expect(screen.getByText("基础版")).toBeTruthy();
    expect(screen.getByText("高级版")).toBeTruthy();
    expect(screen.getByText("旗舰版")).toBeTruthy();
    expect(screen.getByText("覆盖日常使用")).toBeTruthy();
    expect(screen.getByText("支持高频使用")).toBeTruthy();
    expect(screen.getByText("满足重度需求")).toBeTruthy();
    expect(screen.getByText("¥19.9")).toBeTruthy();
    expect(screen.getByText("¥49.9")).toBeTruthy();
    expect(screen.getByText("¥89")).toBeTruthy();
    expect(screen.getAllByText("DeepSeek 约官网 4 倍用量")).toHaveLength(3);
    expect(screen.getByText("推荐")).toBeTruthy();
    expect(screen.queryByText("当前")).toBeNull();
    const buyLinks = screen.getAllByRole("link", { name: "去购买" });
    expect(buyLinks).toHaveLength(3);
    expect(buyLinks.map((el) => el.getAttribute("href"))).toEqual([
      SEAT_STORE_URL,
      SEAT_STORE_URL,
      SEAT_STORE_URL,
    ]);
    expect(screen.getByRole("textbox", { name: "兑换码" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "兑换" })).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText(/收款码/)).toBeNull();
    expect(screen.queryByText(/扫码/)).toBeNull();
    expect(screen.getByText(/链动小铺/)).toBeTruthy();
    expect(screen.getByText(/席位 30 天/)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(
      new RegExp(`测试阶段请先联系管理员下单：QQ\\s*${SUBSCRIPTION_ORDER_QQ}`),
    );
  });

  it("marks the matching published seat as 当前 and turns its CTA into 续费", () => {
    renderPage({
      currentPlanId: "plus",
      seatUntil: "2026-11-17T00:00:00Z",
    });
    expect(screen.getByText("当前")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "续费" }).getAttribute("href"),
    ).toBe(SEAT_STORE_URL);
    expect(screen.getAllByRole("link", { name: "去购买" })).toHaveLength(2);
    expect(
      screen.getByText(
        new RegExp(`至 ${formatSeatUntil("2026-11-17T00:00:00Z")}`),
      ),
    ).toBeTruthy();
  });

  it("names a queued downgrade on the current card", () => {
    renderPage({
      currentPlanId: "flagship",
      seatUntil: "2026-11-17T00:00:00Z",
      pendingPlanId: "plus",
    });
    expect(
      screen.getByText(
        new RegExp(
          `至 ${formatSeatUntil("2026-11-17T00:00:00Z")}，其后改为高级版`,
        ),
      ),
    ).toBeTruthy();
  });

  it("still opens the shop when the store URL is empty", () => {
    renderPage({ storeUrl: "" });
    expect(screen.queryByText("购买入口未配置")).toBeNull();
    const buyLinks = screen.getAllByRole("link", { name: "去购买" });
    expect(buyLinks).toHaveLength(3);
    expect(buyLinks[0]?.getAttribute("href")).toBe(SEAT_STORE_URL);
  });
});
