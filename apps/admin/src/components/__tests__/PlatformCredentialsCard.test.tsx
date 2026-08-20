// @vitest-environment jsdom
/**
 * Admin 系统页「平台额度账号」卡：checklist、空池回落提示、禁用。
 * The leading block comment keeps the @vitest-environment directive file-leading.
 */

import { PlatformCredentialsCard } from "@/components/PlatformCredentialsCard";
import {
  clearPlatformCredentialRuntime,
  createPlatformCredential,
  deletePlatformCredential,
  listPlatformCredentials,
  updatePlatformCredential,
} from "@/services/adminPlatformCredentials";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

vi.mock("@/services/adminPlatformCredentials", () => ({
  listPlatformCredentials: vi.fn(),
  createPlatformCredential: vi.fn(),
  updatePlatformCredential: vi.fn(),
  deletePlatformCredential: vi.fn(),
  clearPlatformCredentialRuntime: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const row = {
  id: "11111111-1111-1111-1111-111111111111",
  label: "Go-A",
  base_url: "https://opencode.ai/zen/go/v1",
  subscription_day: 18,
  enabled: true,
  masked_key: "••••aaaa",
  created_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
  status: "healthy" as const,
  recovery_at: null,
  limit_name: null,
};

describe("PlatformCredentialsCard", () => {
  beforeEach(() => {
    vi.mocked(listPlatformCredentials).mockResolvedValue({
      data: [row],
      fallback: "pool",
    });
  });

  it("shows the China-region opt-in checklist and the member id", async () => {
    render(<PlatformCredentialsCard />);
    expect(
      await screen.findByText(/中国区托管 opt-in/),
    ).toBeTruthy();
    expect(screen.getByText("Go-A")).toBeTruthy();
    expect(screen.getByText(row.id)).toBeTruthy();
    expect(screen.getByText("启用")).toBeTruthy();
    expect(screen.getByText("不限")).toBeTruthy();
  });

  it("disables a member without deleting it", async () => {
    vi.mocked(updatePlatformCredential).mockResolvedValue({
      ...row,
      enabled: false,
    });
    render(<PlatformCredentialsCard />);
    fireEvent.click(await screen.findByText("禁用"));
    await waitFor(() =>
      expect(updatePlatformCredential).toHaveBeenCalledWith(row.id, {
        enabled: false,
      }),
    );
    expect(toast.success).toHaveBeenCalled();
    expect(deletePlatformCredential).not.toHaveBeenCalled();
  });

  it("clears runtime on a blocked member without toggling enabled", async () => {
    const blocked = {
      ...row,
      status: "blocked" as const,
    };
    vi.mocked(listPlatformCredentials).mockResolvedValue({
      data: [blocked],
      fallback: "pool",
    });
    vi.mocked(clearPlatformCredentialRuntime).mockResolvedValue({
      ...blocked,
      status: "healthy",
    });
    render(<PlatformCredentialsCard />);
    fireEvent.click(await screen.findByText("解封"));
    await waitFor(() =>
      expect(clearPlatformCredentialRuntime).toHaveBeenCalledWith(blocked.id),
    );
    expect(toast.success).toHaveBeenCalled();
    expect(updatePlatformCredential).not.toHaveBeenCalled();
  });

  it("reloads the pool when refreshEpoch changes", async () => {
    const { rerender } = render(<PlatformCredentialsCard refreshEpoch={0} />);
    expect(await screen.findByText("Go-A")).toBeTruthy();
    expect(listPlatformCredentials).toHaveBeenCalledTimes(1);

    rerender(<PlatformCredentialsCard refreshEpoch={1} />);
    await waitFor(() =>
      expect(listPlatformCredentials).toHaveBeenCalledTimes(2),
    );
  });

  it("opens the create dialog from 新增", async () => {
    vi.mocked(listPlatformCredentials).mockResolvedValue({
      data: [],
      fallback: "env",
    });
    render(<PlatformCredentialsCard />);
    expect(await screen.findByText(/回落 env/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("新增账号"));
    expect(screen.getByText("新增平台账号")).toBeTruthy();
    expect(screen.getByText(/工具条数上限/)).toBeTruthy();
    expect(createPlatformCredential).not.toHaveBeenCalled();
  });

  it("saves declared tool-surface caps on create", async () => {
    vi.mocked(listPlatformCredentials).mockResolvedValue({
      data: [],
      fallback: "env",
    });
    vi.mocked(createPlatformCredential).mockResolvedValue({
      ...row,
      tool_surface_limits: { max_tools: 16 },
    });
    render(<PlatformCredentialsCard />);
    fireEvent.click(await screen.findByLabelText("新增账号"));
    fireEvent.change(screen.getByPlaceholderText("Go 号 2 · 8 月购"), {
      target: { value: "Go-A" },
    });
    const keyInput = screen.getByLabelText("API Key");
    fireEvent.change(keyInput, { target: { value: "sk-pool-secret-aaaa" } });
    fireEvent.change(screen.getByLabelText(/工具条数上限/), {
      target: { value: "16" },
    });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() =>
      expect(createPlatformCredential).toHaveBeenCalledWith(
        expect.objectContaining({
          label: "Go-A",
          tool_surface_limits: { max_tools: 16 },
        }),
      ),
    );
  });
});
