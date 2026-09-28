// @vitest-environment jsdom
/**
 * QuotaDialog test — pins the 日成本 (daily cost) override dimension.
 * Services mocked so no real HTTP; asserts the field prefills from
 * `quota_daily_cost_cny` and that save sends it (value → number, empty → null =
 * 继承全局) through the tri-state PATCH.
 */
import { QuotaDialog } from "@/components/QuotaDialog";
import { type AdminUser, updateUser } from "@/services/adminUsers";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminUsers", () => ({ updateUser: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeUser(p: Partial<AdminUser> = {}): AdminUser {
  return {
    id: "u1",
    username: "alice",
    display_name: "Alice",
    email: null,
    role: "user",
    status: "active",
    is_unlimited: false,
    quota_daily_tokens: null,
    quota_monthly_cost_cny: null,
    quota_daily_cost_cny: null,
    created_at: "2026-06-01T00:00:00Z",
    deleted_at: null,
    ...p,
  };
}

const DAILY_COST_LABEL = "日成本上限（元 / 日）";

describe("QuotaDialog daily-cost dimension", () => {
  it("prefills quota_daily_cost_cny and sends the edited value on save", async () => {
    vi.mocked(updateUser).mockResolvedValue(makeUser());
    render(
      <QuotaDialog
        user={makeUser({ quota_daily_cost_cny: 1.5 })}
        onClose={() => undefined}
        onSaved={() => undefined}
      />,
    );

    const field = screen.getByLabelText(DAILY_COST_LABEL) as HTMLInputElement;
    expect(field.value).toBe("1.5");

    fireEvent.change(field, { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith(
        "u1",
        expect.objectContaining({ quota_daily_cost_cny: 3 }),
      ),
    );
  });

  it("sends null for an empty daily-cost override (inherit global)", async () => {
    vi.mocked(updateUser).mockResolvedValue(makeUser());
    render(
      <QuotaDialog
        user={makeUser()}
        onClose={() => undefined}
        onSaved={() => undefined}
      />,
    );

    const field = screen.getByLabelText(DAILY_COST_LABEL) as HTMLInputElement;
    expect(field.value).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith(
        "u1",
        expect.objectContaining({ quota_daily_cost_cny: null }),
      ),
    );
  });
});
