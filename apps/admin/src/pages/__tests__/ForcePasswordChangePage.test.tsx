// @vitest-environment jsdom
/**
 * Forced password change (临时密码首登关卡). An account can sit behind *two* gates at
 * once — temp password and MFA enrollment — and `App` checks the password gate first.
 * Clearing that gate therefore has to re-derive the whole session, not just mark it
 * authenticated: the store defaults `mfaSetupRequired` to false, which would drop the
 * MFA gate while the backend keeps 428-ing every request with no route back.
 */

import { ForcePasswordChangePage } from "@/pages/ForcePasswordChangePage";
import { changePassword, fetchMe, mfaStatus } from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `applySession` stays real — it is the thing under test — so only the wire calls
// it makes are stubbed.
vi.mock("@/services/auth", () => ({
  changePassword: vi.fn(() => Promise.resolve()),
  fetchMe: vi.fn(),
  mfaStatus: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const ADMIN = {
  id: "u1",
  username: "root",
  displayName: "Root",
  email: null,
  emailVerifiedAt: null,
  role: "admin",
  passwordMustChange: false,
};

beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    user: { ...ADMIN, passwordMustChange: true },
    mfaSetupRequired: false,
    pendingMfaToken: null,
  });
  vi.mocked(fetchMe).mockResolvedValue(ADMIN);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function submitNewPassword() {
  render(<ForcePasswordChangePage />);
  fireEvent.change(screen.getByPlaceholderText(/当前密码/), {
    target: { value: "temp-pass-1" },
  });
  fireEvent.change(screen.getByPlaceholderText(/新密码（至少/), {
    target: { value: "brand-new-pass" },
  });
  fireEvent.change(screen.getByPlaceholderText("确认新密码"), {
    target: { value: "brand-new-pass" },
  });
  fireEvent.click(screen.getByRole("button", { name: /确认并进入后台/ }));
}

describe("ForcePasswordChangePage", () => {
  it("keeps the MFA enrollment gate up when the account still needs to enroll", async () => {
    vi.mocked(mfaStatus).mockResolvedValue({ enrolled: false, required: true });
    submitNewPassword();

    await waitFor(() => expect(changePassword).toHaveBeenCalledWith("temp-pass-1", "brand-new-pass"));
    await waitFor(() => expect(useAuthStore.getState().mfaSetupRequired).toBe(true));
    expect(useAuthStore.getState().user?.passwordMustChange).toBe(false);
  });

  it("lets an already-enrolled admin straight through", async () => {
    vi.mocked(mfaStatus).mockResolvedValue({ enrolled: true, required: true });
    submitNewPassword();

    await waitFor(() => expect(useAuthStore.getState().status).toBe("authenticated"));
    expect(useAuthStore.getState().mfaSetupRequired).toBe(false);
  });

  it("blocks submission until the two new passwords agree", () => {
    render(<ForcePasswordChangePage />);
    const submit = screen.getByRole("button", {
      name: /确认并进入后台/,
    }) as HTMLButtonElement;

    fireEvent.change(screen.getByPlaceholderText(/当前密码/), {
      target: { value: "temp-pass-1" },
    });
    fireEvent.change(screen.getByPlaceholderText(/新密码（至少/), {
      target: { value: "brand-new-pass" },
    });
    fireEvent.change(screen.getByPlaceholderText("确认新密码"), {
      target: { value: "mismatch" },
    });

    expect(submit.disabled).toBe(true);
    expect(screen.getByText("两次输入的新密码不一致")).toBeTruthy();
    expect(changePassword).not.toHaveBeenCalled();
  });
});
