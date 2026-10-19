/**
 * Host main-process op unit tests (no Electron shell side effects for ping/info).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  ipcMain: { handle: vi.fn() },
  shell: { openExternal: vi.fn(async () => undefined) },
  Notification: class {
    static isSupported() {
      return false;
    }
    show() {}
  },
}));

vi.mock("../log-service", () => ({
  logDesktop: vi.fn(),
}));

import { runHostOp } from "../host-service";
import { logDesktop } from "../log-service";

describe("runHostOp", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("host_ping returns ok envelope", async () => {
    const result = await runHostOp({ op: "host_ping" });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.ok).toBe(true);
      expect(result.value.platform).toBeTruthy();
    }
  });

  it("host_info returns machine facts", async () => {
    const result = await runHostOp({ op: "host_info" });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.platform).toBeTruthy();
      expect(typeof result.value.total_mem_mb).toBe("number");
    }
  });

  it("host_network_summary returns iface summary without scan note", async () => {
    const result = await runHostOp({ op: "host_network_summary" });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.note).toBe("local_iface_summary_no_port_scan");
      expect(Array.isArray(result.value.adapters)).toBe(true);
    }
  });

  it("retired host ops are unknown", async () => {
    for (const op of [
      "host_open_settings",
      "host_audio_devices",
      "host_audio_set_default",
      "host_service_restart",
      "host_os_log_summary",
      "host_package_install",
    ]) {
      const result = await runHostOp({ op });
      expect(result.ok).toBe(false);
    }
  });

  it("unknown op fails honestly", async () => {
    const result = await runHostOp({ op: "host_wipe_disk" });
    expect(result.ok).toBe(false);
  });

  it("host_shell rejects empty command", async () => {
    const result = await runHostOp({
      op: "host_shell",
      args: { command: "  " },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("HostShellEmptyCommand");
    }
  });

  it("host_shell fuse blocks rm -rf /", async () => {
    const result = await runHostOp({
      op: "host_shell",
      args: { command: "rm -rf /" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("HostShellFuse");
    }
  });

  it("host_shell rejects cmd-style %VAR% env", async () => {
    const result = await runHostOp({
      op: "host_shell",
      args: { command: "Get-ChildItem '%APPDATA%\\Cursor\\logs'" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("HostShellIdiom");
      expect(result.error.detail).toMatch(/\$env:/);
    }
  });

  it("host_shell rejects bash || chain on Windows", async () => {
    if (process.platform !== "win32") return;
    const result = await runHostOp({
      op: "host_shell",
      args: {
        command: "Test-Path $env:APPDATA\\Cursor\\logs || echo missing",
      },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("HostShellIdiom");
      expect(result.error.detail).toMatch(/\|\|/);
    }
  });

  it("host_shell runs a trivial command", async () => {
    const command =
      process.platform === "win32" ? "Write-Output 'p3ok'" : "echo p3ok";
    const result = await runHostOp({
      op: "host_shell",
      args: { command, timeout_seconds: 15 },
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.timed_out).toBe(false);
      expect(result.value.exit_code).toBe(0);
      expect(String(result.value.stdout)).toContain("p3ok");
      expect(result.value.obs_env).toBeTruthy();
      expect(result.value.obs_windows).toBeUndefined();
    }
    expect(logDesktop).toHaveBeenCalledWith(
      expect.objectContaining({
        event: "desktop.host_shell_env_fingerprint",
      }),
    );
  });

  it("host_shell times out and reports honestly", async () => {
    const command =
      process.platform === "win32" ? "Start-Sleep -Seconds 5" : "sleep 5";
    const result = await runHostOp({
      op: "host_shell",
      args: { command, timeout_seconds: 1 },
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.timed_out).toBe(true);
      expect(result.value.exit_code).toBeNull();
      expect(result.value.obs_env).toBeTruthy();
    }
  }, 15_000);

  it("host_shell GUI-launch command attaches obs_windows snapshot", async () => {
    if (process.platform !== "win32") return;
    // 不真开 GUI：用 Start-Process 跑短暂无窗进程，仍命中 looksLikeGuiLaunch。
    const result = await runHostOp({
      op: "host_shell",
      args: {
        command:
          "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c exit 0' -WindowStyle Hidden -Wait",
        timeout_seconds: 20,
      },
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.obs_env).toBeTruthy();
      expect(Array.isArray(result.value.obs_windows)).toBe(true);
    }
    expect(logDesktop).toHaveBeenCalledWith(
      expect.objectContaining({
        event: "desktop.host_shell_windows_snapshot",
      }),
    );
  }, 30_000);
});
