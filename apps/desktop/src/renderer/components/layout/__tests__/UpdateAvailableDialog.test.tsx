// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", () => ({
  hasAutoUpdater: vi.fn(() => true),
  hasLocalFiles: () => true,
}));
vi.mock("@/lib/clientBuildInfo", () => ({
  clientVersion: vi.fn(() => "0.6.1"),
}));

import { hasAutoUpdater } from "@/lib/capabilities";
import { useUpdatesStore } from "@/stores/updates";
import { UpdateAvailableDialog } from "../UpdateAvailableDialog";

const hasAutoUpdaterMock = vi.mocked(hasAutoUpdater);

beforeEach(() => {
  hasAutoUpdaterMock.mockReturnValue(true);
  useUpdatesStore.setState({
    status: {
      phase: "available",
      version: "0.7.0",
      releaseNotes: "重要修复",
      sizeBytes: 2048,
      autoInstallCapable: true,
    },
    dialogOpen: true,
    outdatedMinVersion: null,
    download: vi.fn(() => Promise.resolve()),
    remindLater: vi.fn(),
    skipVersion: vi.fn(),
    closeUpdateDialog: vi.fn(),
    install: vi.fn(() => Promise.resolve()),
  });
});

afterEach(() => {
  cleanup();
  useUpdatesStore.setState({
    status: { phase: "idle", autoInstallCapable: true },
    dialogOpen: false,
    outdatedMinVersion: null,
  });
});

describe("UpdateAvailableDialog", () => {
  it("renders version, notes, size and three actions", () => {
    render(<UpdateAvailableDialog />);
    expect(screen.getByText("发现新版本 0.7.0")).toBeTruthy();
    expect(screen.getByText(/当前版本 0\.6\.1/)).toBeTruthy();
    expect(screen.getByText("重要修复")).toBeTruthy();
    expect(screen.getByRole("button", { name: "下载安装包" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "稍后提醒" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "跳过此版本" })).toBeTruthy();
  });

  it("falls back to default notes when feed empty", () => {
    useUpdatesStore.setState({
      status: {
        phase: "available",
        version: "0.7.0",
        releaseNotes: null,
        autoInstallCapable: true,
      },
    });
    render(<UpdateAvailableDialog />);
    expect(screen.getByText("修复与体验改进")).toBeTruthy();
  });

  it("calls download on 下载安装包", () => {
    const download = vi.fn(() => Promise.resolve());
    useUpdatesStore.setState({ download });
    render(<UpdateAvailableDialog />);
    fireEvent.click(screen.getByRole("button", { name: "下载安装包" }));
    expect(download).toHaveBeenCalled();
  });

  it("autoInstallCapable:false still offers in-app installer download", () => {
    const download = vi.fn(() => Promise.resolve());
    useUpdatesStore.setState({
      download,
      status: {
        phase: "available",
        version: "0.7.0",
        releaseNotes: "重要修复",
        autoInstallCapable: false,
      },
    });
    render(<UpdateAvailableDialog />);
    expect(screen.queryByText(/此版本需手动下载安装/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "下载安装包" }));
    expect(download).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "稍后提醒" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "跳过此版本" })).toBeTruthy();
  });

  it("soft update does not keep a download-progress dialog", () => {
    useUpdatesStore.setState({
      status: {
        phase: "downloading",
        version: "0.7.0",
        percent: 42,
        bytesPerSecond: 524_288,
        transferred: 83_886_080,
        total: 198_180_864,
        autoInstallCapable: true,
      },
      dialogOpen: true,
      outdatedMinVersion: null,
    });
    render(<UpdateAvailableDialog />);
    expect(screen.queryByText(/下载进度/)).toBeNull();
    expect(screen.queryByRole("button", { name: "后台下载" })).toBeNull();
    expect(screen.queryByRole("button", { name: "下载安装包" })).toBeNull();
  });

  it("force gate still shows download progress in dialog", () => {
    useUpdatesStore.setState({
      status: {
        phase: "downloading",
        version: "0.7.0",
        percent: 42,
        bytesPerSecond: 524_288,
        transferred: 83_886_080,
        total: 198_180_864,
        autoInstallCapable: true,
      },
      dialogOpen: true,
      outdatedMinVersion: "0.6.5",
    });
    render(<UpdateAvailableDialog />);
    expect(
      screen.getByText(/下载进度 42% · 80 MB \/ 189 MB · 512 KB\/s/),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "下载中…" })).toBeTruthy();
  });

  it("hides on web clients", () => {
    hasAutoUpdaterMock.mockReturnValue(false);
    const { container } = render(<UpdateAvailableDialog />);
    expect(container.firstChild).toBeNull();
  });

  it("hides skip / later under force-update hard gate", () => {
    useUpdatesStore.setState({ outdatedMinVersion: "0.6.5" });
    render(<UpdateAvailableDialog />);
    expect(screen.getByRole("button", { name: "下载安装包" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "稍后提醒" })).toBeNull();
    expect(screen.queryByRole("button", { name: "跳过此版本" })).toBeNull();
    expect(screen.queryByRole("button", { name: "关闭" })).toBeNull();
  });
});
