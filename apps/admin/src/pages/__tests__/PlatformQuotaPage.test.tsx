// @vitest-environment jsdom
/**
 * 平台额度页：号池卡 + 全局额度/计费只读快照。钉住「改 env 后需重启、无热更」
 * 出现在只读块上，以及号池卡在此页渲染（系统页不再挂它）。
 * The leading block comment keeps the @vitest-environment directive file-leading.
 */

import { PlatformQuotaPage } from "@/pages/PlatformQuotaPage";
import { listPlatformCredentials } from "@/services/adminPlatformCredentials";
import {
  type AdminSystemStatus,
  fetchSystemStatus,
} from "@/services/adminSystem";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminSystem", () => ({ fetchSystemStatus: vi.fn() }));
vi.mock("@/services/adminPlatformCredentials", () => ({
  listPlatformCredentials: vi.fn().mockResolvedValue({ data: [], fallback: "env" }),
  createPlatformCredential: vi.fn(),
  updatePlatformCredential: vi.fn(),
  deletePlatformCredential: vi.fn(),
  clearPlatformCredentialRuntime: vi.fn(),
}));

const fetchSystemStatusMock = vi.mocked(fetchSystemStatus);

function status(overrides: Partial<AdminSystemStatus> = {}): AdminSystemStatus {
  return {
    billing_mode: "platform",
    quota: {
      daily_tokens: 1_000_000,
      daily_requests: 100,
      daily_cost_nano: 0,
      monthly_cost_nano: 5_000_000_000,
    },
    database_ok: true,
    version: "0.3.1",
    git_sha: "unknown",
    built_at: "unknown",
    users_total: 1,
    users_active: 1,
    admins: 1,
    ...overrides,
  };
}

function renderQuota() {
  return render(
    <MemoryRouter>
      <PlatformQuotaPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PlatformQuotaPage", () => {
  it("renders billing mode, global defaults, and the restart caveat", async () => {
    fetchSystemStatusMock.mockResolvedValue(status());
    renderQuota();

    expect(await screen.findByText("计费模式")).toBeTruthy();
    expect(screen.getByText("平台付费")).toBeTruthy();
    expect(screen.getByText("全局配额默认值")).toBeTruthy();
    expect(screen.getByText("月成本").parentElement?.textContent).toContain("¥5.00");
    expect(screen.getAllByText(/改 env 后需重启、无热更/).length).toBeGreaterThan(0);
  });

  it("shows BYOK framing when billing_mode is byok", async () => {
    fetchSystemStatusMock.mockResolvedValue(status({ billing_mode: "byok" }));
    renderQuota();

    expect(await screen.findByText("BYOK · 自带 Key")).toBeTruthy();
    expect(screen.getByText(/配额防线休眠/)).toBeTruthy();
  });

  it("renders the credential pool card independently of the snapshot", async () => {
    fetchSystemStatusMock.mockResolvedValue(status());
    renderQuota();

    expect(await screen.findByText("平台额度账号")).toBeTruthy();
  });

  it("still shows the pool card when the snapshot request fails", async () => {
    fetchSystemStatusMock.mockRejectedValue(new Error("down"));
    renderQuota();

    expect(await screen.findByText("发生未知错误")).toBeTruthy();
    expect(screen.queryByText("计费模式")).toBeNull();
    expect(await screen.findByText("平台额度账号")).toBeTruthy();
  });

  it("header refresh also reloads the credential pool", async () => {
    fetchSystemStatusMock.mockResolvedValue(status());
    renderQuota();
    expect(await screen.findByText("平台付费")).toBeTruthy();

    await waitFor(() =>
      expect(vi.mocked(listPlatformCredentials).mock.calls.length).toBeGreaterThan(
        0,
      ),
    );
    const atMount = vi.mocked(listPlatformCredentials).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() =>
      expect(vi.mocked(listPlatformCredentials).mock.calls.length).toBeGreaterThan(
        atMount,
      ),
    );
  });

  it("keeps the previous snapshot on screen while refreshing", async () => {
    fetchSystemStatusMock.mockResolvedValue(status());
    renderQuota();
    await screen.findByText("平台付费");

    let resolve!: (v: AdminSystemStatus) => void;
    fetchSystemStatusMock.mockReturnValue(
      new Promise<AdminSystemStatus>((r) => {
        resolve = r;
      }),
    );
    const refresh = screen.getByRole("button", { name: "刷新" });
    fireEvent.click(refresh);

    await waitFor(() =>
      expect(document.querySelector('[aria-busy="true"]')).toBeTruthy(),
    );
    expect(screen.getByText("平台付费")).toBeTruthy();

    resolve(status({ billing_mode: "byok" }));
    expect(await screen.findByText("BYOK · 自带 Key")).toBeTruthy();
  });
});
