// @vitest-environment jsdom
import { useAuthStore } from "@/stores/auth";
import { useServerHealthStore } from "@/stores/serverHealth";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "../AuthGate";

const bootstrapAuth = vi.fn();
const diagnoseOutage = vi.fn();
const hasOfflineCache = vi.fn();
const hydrateOfflineShell = vi.fn();

vi.mock("@/services/auth", () => ({
  bootstrapAuth: (...args: unknown[]) => bootstrapAuth(...args),
  diagnoseOutage: (...args: unknown[]) => diagnoseOutage(...args),
}));

vi.mock("@/services/offlineCache", () => ({
  cacheShellMeta: vi.fn(),
  clearOfflineCache: vi.fn(),
  hasOfflineCache: (...args: unknown[]) => hasOfflineCache(...args),
  hydrateOfflineShell: (...args: unknown[]) => hydrateOfflineShell(...args),
}));

vi.mock("@/services/api", () => ({
  setServiceUnavailableHandler: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));

vi.mock("@/services/defaultWorkspace", () => ({
  ensureDefaultContainerRoot: vi.fn(),
}));

vi.mock("@/services/serverHealth", () => ({
  confirmMidSessionOutage: vi.fn(),
}));

vi.mock("@/lib/capabilities", () => ({
  isWebClient: () => false,
  isNativeRuntime: () => false,
}));
vi.mock("@/lib/log", () => ({ logEvent: vi.fn() }));
vi.mock("@/components/layout/TitleBar", () => ({
  MinimalTitleBar: () => null,
}));
vi.mock("@/pages/LoginPage", () => ({ LoginPage: () => null }));
vi.mock("@/pages/ServiceUnavailablePage", () => ({
  ServiceUnavailablePage: () => null,
}));

const USER = {
  id: "u1",
  username: "alice",
  displayName: "Alice",
  email: null,
  emailVerifiedAt: null,
  role: "user",
  avatarUrl: null,
};

/** Cold-start outage that still has a local cache → offline read-only shell. */
function bootstrapOutage() {
  bootstrapAuth.mockResolvedValue({
    kind: "unavailable",
    reason: "后端不可用",
  });
  hydrateOfflineShell.mockResolvedValue(USER);
}

async function renderGate() {
  render(
    <AuthGate>
      <div data-testid="shell" />
    </AuthGate>,
  );
  await waitFor(() => expect(bootstrapAuth).toHaveBeenCalled());
}

/** Drive the connectivity edge the health heartbeat produces on recovery. */
async function reconnect() {
  await act(async () => {
    useServerHealthStore.getState().markOnline();
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  hasOfflineCache.mockResolvedValue(true);
  diagnoseOutage.mockResolvedValue(null);
  useAuthStore.setState({
    status: "loading",
    user: null,
    sessionVerified: false,
    reason: null,
  });
  useServerHealthStore.setState({
    status: "checking",
    lastOkAt: null,
    reason: null,
    justRecovered: false,
    offlineSince: null,
  });
});

afterEach(cleanup);

describe("AuthGate · 离线只读会话补正", () => {
  it("离线只读进壳的会话标记为未验证（服务端从未确认过）", async () => {
    bootstrapOutage();

    await renderGate();

    await waitFor(() => {
      expect(useAuthStore.getState().status).toBe("authenticated");
    });
    expect(useAuthStore.getState().sessionVerified).toBe(false);
    expect(useServerHealthStore.getState().status).toBe("offline");
  });

  it("恢复联通后重跑权威握手，会话补正为已验证", async () => {
    bootstrapOutage();
    await renderGate();
    await waitFor(() =>
      expect(useServerHealthStore.getState().status).toBe("offline"),
    );

    bootstrapAuth.mockResolvedValue({ kind: "authenticated", user: USER });
    await reconnect();

    await waitFor(() => {
      expect(useAuthStore.getState().sessionVerified).toBe(true);
    });
    // 补正复用同一条 bootstrap，而不是单独去补某个令牌。
    expect(bootstrapAuth).toHaveBeenCalledTimes(2);
  });

  it("已验证的会话不会因为断线重连再次握手", async () => {
    bootstrapAuth.mockResolvedValue({ kind: "authenticated", user: USER });
    await renderGate();
    await waitFor(() =>
      expect(useAuthStore.getState().sessionVerified).toBe(true),
    );

    await act(async () => {
      useServerHealthStore.getState().markOffline("断线", "heartbeat");
    });
    await reconnect();

    expect(bootstrapAuth).toHaveBeenCalledTimes(1);
  });

  it("补正失败仍留在离线只读，下一个 online 边沿再试", async () => {
    bootstrapOutage();
    await renderGate();
    await waitFor(() =>
      expect(useServerHealthStore.getState().status).toBe("offline"),
    );

    // 第一次补正又撞上不可用 → 重新落回离线只读，会话仍未验证。
    await reconnect();
    await waitFor(() => expect(bootstrapAuth).toHaveBeenCalledTimes(2));
    expect(useAuthStore.getState().sessionVerified).toBe(false);
    expect(useServerHealthStore.getState().status).toBe("offline");

    bootstrapAuth.mockResolvedValue({ kind: "authenticated", user: USER });
    await reconnect();

    await waitFor(() => {
      expect(useAuthStore.getState().sessionVerified).toBe(true);
    });
    expect(bootstrapAuth).toHaveBeenCalledTimes(3);
  });
});
