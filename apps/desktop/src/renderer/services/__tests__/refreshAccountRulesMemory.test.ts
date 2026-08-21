// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/accountToken", () => ({
  resolveSidecarAccountAuth: vi.fn(),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: {
    getState: () => ({ user: { id: "user-1" } }),
  },
}));

import { resolveSidecarAccountAuth } from "@/services/accountToken";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";

const accountAuth = {
  baseUrl: "https://api.example.com/v1/account",
  apiKey: "acct-tok",
};

describe("scheduleAccountRulesMemoryRefresh", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sidecarApi = {
      refreshLiveAccountRulesMemory: vi.fn().mockResolvedValue(undefined),
    } as unknown as typeof window.sidecarApi;
  });

  it("kicks live-sidecar refresh when a ticket is available", async () => {
    vi.mocked(resolveSidecarAccountAuth).mockResolvedValue(accountAuth);
    scheduleAccountRulesMemoryRefresh();
    await vi.waitFor(() => {
      expect(
        window.sidecarApi.refreshLiveAccountRulesMemory,
      ).toHaveBeenCalledWith({
        accountAuth,
        userId: "user-1",
      });
    });
  });

  it("skips when there is no account ticket", async () => {
    vi.mocked(resolveSidecarAccountAuth).mockResolvedValue(null);
    scheduleAccountRulesMemoryRefresh();
    await Promise.resolve();
    await Promise.resolve();
    expect(
      window.sidecarApi.refreshLiveAccountRulesMemory,
    ).not.toHaveBeenCalled();
  });
});
