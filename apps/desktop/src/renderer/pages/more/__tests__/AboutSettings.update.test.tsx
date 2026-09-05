// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/capabilities")>()),
  hasAutoUpdater: vi.fn(() => true),
  hasLocalEngine: vi.fn(() => false),
  isWebRuntime: vi.fn(() => false),
}));
vi.mock("@/lib/clientBuildInfo", () => ({
  clientVersion: vi.fn(() => "0.6.1"),
  clientGitSha: vi.fn(() => "abcdef1"),
  formatGitSha: (sha: string) => sha,
}));
vi.mock("@/services/system", () => ({
  fetchVersion: vi.fn(() =>
    Promise.resolve({
      version: "1.0.0",
      gitSha: "deadbeef",
      builtAt: "2026-01-01T00:00:00Z",
    }),
  ),
}));

import { useUpdatesStore } from "@/stores/updates";
import { AboutSettings } from "../AboutSettings";

beforeEach(() => {
  useUpdatesStore.setState({
    status: { phase: "idle", autoInstallCapable: true },
    dialogOpen: false,
    outdatedMinVersion: null,
    openUpdateDialog: vi.fn(),
    check: vi.fn(() => Promise.resolve()),
    install: vi.fn(() => Promise.resolve()),
  });
});

afterEach(() => {
  cleanup();
  useUpdatesStore.setState({
    status: { phase: "idle", autoInstallCapable: true },
    dialogOpen: false,
  });
});

describe("AboutSettings software update", () => {
  it("shows installer-download copy and 查看更新 when a version is available", async () => {
    useUpdatesStore.setState({
      status: {
        phase: "available",
        version: "0.7.0",
        autoInstallCapable: false,
      },
    });
    render(
      <MemoryRouter>
        <AboutSettings />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(
        screen.getByText(/发现新版本 0\.7\.0，确认后下载安装包/),
      ).toBeTruthy();
    });
    expect(screen.queryByRole("link", { name: "前往下载页" })).toBeNull();
    expect(screen.getByRole("button", { name: "查看更新" })).toBeTruthy();
  });

  it("shows 打开安装包 when downloaded", async () => {
    const install = vi.fn(() => Promise.resolve());
    useUpdatesStore.setState({
      install,
      status: {
        phase: "downloaded",
        version: "0.7.0",
        autoInstallCapable: true,
      },
    });
    render(
      <MemoryRouter>
        <AboutSettings />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "打开安装包" })).toBeTruthy();
    });
  });
});
