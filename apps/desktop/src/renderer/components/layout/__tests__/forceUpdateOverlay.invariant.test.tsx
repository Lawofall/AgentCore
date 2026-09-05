// @vitest-environment jsdom
/**
 * 不可关闭遮罩层不变量：硬闸门禁 / force 态说明窗在任意 phase 下都至少有一个
 * 真正可用的动作；error 态「重试下载」必须真的调用 download。
 */
import type { UpdaterPhase, UpdaterStatus } from "@shared/updater-contract";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", () => ({
  hasAutoUpdater: vi.fn(() => true),
  hasLocalFiles: () => true,
}));
vi.mock("@/lib/clientBuildInfo", () => ({
  clientVersion: vi.fn(() => "0.6.1"),
}));

import { useUpdatesStore } from "@/stores/updates";
import { ForceUpdateGate } from "../ForceUpdateGate";
import { UpdateAvailableDialog } from "../UpdateAvailableDialog";

function capable(
  status: UpdaterPhase,
  autoInstallCapable = true,
): UpdaterStatus {
  return { ...status, autoInstallCapable };
}

/** 可点击的按钮或带 href 的链接（禁用按钮不算）。 */
function usableActions(root: HTMLElement): HTMLElement[] {
  const buttons = [...root.querySelectorAll("button")].filter(
    (el) => !(el as HTMLButtonElement).disabled,
  );
  const links = [...root.querySelectorAll("a[href]")];
  return [...buttons, ...links] as HTMLElement[];
}

const FORCE_PHASES: UpdaterStatus[] = [
  capable({ phase: "idle" }, false),
  capable({ phase: "checking" }, false),
  capable({ phase: "not-available" }, false),
  capable({ phase: "available", version: "0.7.0" }, false),
  capable({ phase: "error", message: "network down" }, false),
  capable({ phase: "idle" }, true),
  capable({ phase: "checking" }, true),
  capable({ phase: "available", version: "0.7.0" }, true),
  capable(
    {
      phase: "downloading",
      version: "0.7.0",
      percent: 42,
      bytesPerSecond: 1024,
      transferred: 1000,
      total: 2000,
    },
    true,
  ),
  capable({ phase: "downloaded", version: "0.7.0" }, true),
  capable({ phase: "error", message: "network down" }, true),
];

beforeEach(() => {
  useUpdatesStore.setState({
    status: capable({ phase: "idle" }),
    outdatedMinVersion: "0.6.5",
    dialogOpen: false,
    check: vi.fn(() => Promise.resolve()),
    download: vi.fn(() => Promise.resolve()),
    install: vi.fn(() => Promise.resolve()),
    openUpdateDialog: vi.fn(),
    closeUpdateDialog: vi.fn(),
    remindLater: vi.fn(),
    skipVersion: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  useUpdatesStore.setState({
    outdatedMinVersion: null,
    status: capable({ phase: "idle" }),
    dialogOpen: false,
  });
});

describe("force-update overlay usable-action invariant", () => {
  it("ForceUpdateGate keeps ≥1 usable action for every phase", () => {
    for (const status of FORCE_PHASES) {
      cleanup();
      useUpdatesStore.setState({
        status,
        outdatedMinVersion: "0.6.5",
        dialogOpen: false,
      });
      const { container } = render(<ForceUpdateGate />);
      expect(
        usableActions(container).length,
        `ForceUpdateGate phase=${status.phase} capable=${status.autoInstallCapable}`,
      ).toBeGreaterThan(0);
    }
  });

  it("force UpdateAvailableDialog keeps ≥1 usable action for dialog phases", () => {
    const dialogPhases: UpdaterStatus[] = [
      capable(
        { phase: "available", version: "0.7.0", releaseNotes: "x" },
        false,
      ),
      capable({ phase: "error", message: "network down" }, false),
      capable(
        { phase: "available", version: "0.7.0", releaseNotes: "x" },
        true,
      ),
      capable(
        {
          phase: "downloading",
          version: "0.7.0",
          percent: 10,
          bytesPerSecond: 1,
          transferred: 1,
          total: 10,
        },
        true,
      ),
      capable({ phase: "downloaded", version: "0.7.0" }, true),
      capable({ phase: "error", message: "network down" }, true),
    ];
    for (const status of dialogPhases) {
      cleanup();
      useUpdatesStore.setState({
        status,
        outdatedMinVersion: "0.6.5",
        dialogOpen: true,
      });
      render(<UpdateAvailableDialog />);
      // Radix Dialog portals into document.body.
      const dialog = screen.getByRole("dialog");
      expect(
        usableActions(dialog).length,
        `force dialog phase=${status.phase} capable=${status.autoInstallCapable}`,
      ).toBeGreaterThan(0);
    }
  });

  it("unsigned mac + hard gate + error: retry download is live, plus download-page hatch", () => {
    const download = vi.fn(() => Promise.resolve());
    useUpdatesStore.setState({
      download,
      outdatedMinVersion: "0.6.5",
      dialogOpen: true,
      status: capable({ phase: "error", message: "network down" }, false),
    });

    const { container: gate } = render(<ForceUpdateGate />);
    expect(
      screen.getAllByRole("link", { name: /前往下载页/ }).length,
    ).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "重试下载" }));
    expect(download).toHaveBeenCalled();
    expect(usableActions(gate).length).toBeGreaterThan(0);

    cleanup();
    useUpdatesStore.setState({
      download,
      outdatedMinVersion: "0.6.5",
      dialogOpen: true,
      status: capable({ phase: "error", message: "network down" }, false),
    });
    render(<UpdateAvailableDialog />);
    fireEvent.click(screen.getByRole("button", { name: "重试下载" }));
    expect(download).toHaveBeenCalledTimes(2);
    expect(usableActions(screen.getByRole("dialog")).length).toBeGreaterThan(0);
  });
});
