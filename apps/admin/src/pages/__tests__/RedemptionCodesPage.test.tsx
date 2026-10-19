// @vitest-environment jsdom
import { RedemptionCodesPage } from "@/pages/RedemptionCodesPage";
import {
  createRedemptionCodes,
  listRedemptionCodes,
} from "@/services/adminRedemptionCodes";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/services/adminRedemptionCodes", () => ({
  listRedemptionCodes: vi.fn(),
  createRedemptionCodes: vi.fn(),
  voidRedemptionCode: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RedemptionCodesPage", () => {
  it("lists unused inventory and issues a batch once", async () => {
    vi.mocked(listRedemptionCodes).mockResolvedValue({
      data: [
        {
          id: "c1",
          sku: "plus",
          status: "unused",
          prefix: "ACP-7K2M",
          batch_id: "b1",
          created_at: "2026-10-18T00:00:00Z",
          redeemed_at: null,
          redeemed_by: null,
        },
      ],
      total: 1,
      unused_starter: 2,
      unused_plus: 4,
      unused_flagship: 1,
    });
    vi.mocked(createRedemptionCodes).mockResolvedValue({
      batch_id: "b2",
      sku: "plus",
      codes: ["ACP-AAAA-BBBB-CCCC"],
    });

    render(
      <MemoryRouter>
        <RedemptionCodesPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("ACP-7K2M")).toBeTruthy();
    expect(screen.getByText("未用高级版").parentElement?.textContent).toContain("4");
    fireEvent.click(screen.getByRole("button", { name: "生成兑换码" }));
    await waitFor(() =>
      expect(createRedemptionCodes).toHaveBeenCalledWith({ sku: "plus", count: 20 }),
    );
    expect(await screen.findByText("ACP-AAAA-BBBB-CCCC")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制全部" })).toBeTruthy();
  });
});
