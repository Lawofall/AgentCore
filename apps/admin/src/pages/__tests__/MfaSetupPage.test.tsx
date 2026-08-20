// @vitest-environment jsdom
/**
 * MFA enrollment wizard. The load-bearing behaviour here is the *exit*: `/mfa/confirm`
 * revokes every session server-side and clears the auth cookies, so finishing the
 * wizard has to drop the client back to the login page. Sending the operator to the
 * dashboard instead bounces them straight back out, reading like a failed enrollment.
 */

import { MfaSetupPage } from "@/pages/MfaSetupPage";
import { clearCsrfToken } from "@/services/api";
import { logout, mfaConfirm, mfaSetup } from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/auth", () => ({
  mfaSetup: vi.fn(),
  mfaConfirm: vi.fn(),
  logout: vi.fn(() => Promise.resolve()),
}));
vi.mock("@/services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/api")>()),
  clearCsrfToken: vi.fn(),
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
  // `clearAllMocks` clears calls but *not* queued `...Once` values, so an unconsumed
  // one would leak into the next test's first call.
  vi.mocked(mfaSetup).mockReset();
  vi.mocked(mfaConfirm).mockReset();
  vi.mocked(logout).mockReset().mockResolvedValue(undefined);
  useAuthStore.setState({
    status: "authenticated",
    user: ADMIN,
    mfaSetupRequired: true,
    pendingMfaToken: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function enrollToRecoveryPhase() {
  vi.mocked(mfaSetup).mockResolvedValue({
    secret: "JBSWY3DPEHPK3PXP",
    otpauth_uri: "otpauth://totp/AgentCore:root?secret=JBSWY3DPEHPK3PXP",
  });
  vi.mocked(mfaConfirm).mockResolvedValue({
    recovery_codes: ["a1b2c3d4e5f6a7b8", "b2c3d4e5f6a7b8c9"],
  });
  render(<MfaSetupPage />);
  fireEvent.change(await screen.findByPlaceholderText(/6 位验证码/), {
    target: { value: "123456" },
  });
  fireEvent.click(screen.getByRole("button", { name: /确认并启用/ }));
  await screen.findByText("保存恢复码");
}

describe("MfaSetupPage", () => {
  it("shows the recovery codes once enrollment is confirmed", async () => {
    await enrollToRecoveryPhase();
    expect(screen.getByText("a1b2c3d4e5f6a7b8")).toBeTruthy();
    expect(screen.getByText(/登出全部设备/)).toBeTruthy();
  });

  it("drops to the login page on finish, because confirm already revoked the session", async () => {
    await enrollToRecoveryPhase();
    fireEvent.click(screen.getByRole("button", { name: /重新登录/ }));
    expect(useAuthStore.getState().status).toBe("unauthenticated");
    // The revoked session's CSRF token dies with it; keeping it would 403 the first
    // mutating request of the *next* login, logout included.
    expect(vi.mocked(clearCsrfToken)).toHaveBeenCalled();
  });

  it("offers retry and sign-out instead of a dead screen when the secret cannot load", async () => {
    vi.mocked(mfaSetup)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://x" });
    render(<MfaSetupPage />);

    await screen.findByRole("button", { name: "重试" });
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    await waitFor(() => expect(logout).toHaveBeenCalled());
    expect(useAuthStore.getState().status).toBe("unauthenticated");
  });

  it("re-requests the secret when retrying", async () => {
    vi.mocked(mfaSetup)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://x" });
    render(<MfaSetupPage />);

    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeTruthy();
    expect(mfaSetup).toHaveBeenCalledTimes(2);
  });
});
