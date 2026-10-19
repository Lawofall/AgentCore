/**
 * Computer Use HostOp dispatch — mocked UIA scripts, no WinForms.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { runGuiAttach, runGuiAct } = vi.hoisted(() => ({
  runGuiAttach: vi.fn(),
  runGuiAct: vi.fn(),
}));

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

vi.mock("../host/gui-uia", () => ({
  runGuiAttach,
  runGuiAct,
}));

import { runHostOp } from "../host-service";
import { clearGuiSession } from "../host/gui-session";

const CID = "conv-gui-1";

describe("host GUI ops", () => {
  const originalPlatform = process.platform;

  beforeEach(() => {
    Object.defineProperty(process, "platform", {
      value: "win32",
      configurable: true,
    });
    runGuiAttach.mockReset();
    runGuiAct.mockReset();
    clearGuiSession(CID);
  });

  afterEach(() => {
    Object.defineProperty(process, "platform", {
      value: originalPlatform,
      configurable: true,
    });
    clearGuiSession(CID);
  });

  it("non-windows attach fails honestly", async () => {
    Object.defineProperty(process, "platform", {
      value: "linux",
      configurable: true,
    });
    const result = await runHostOp({
      op: "host_gui_attach",
      args: { conversation_id: CID },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("HostGuiUnsupportedPlatform");
    }
    expect(runGuiAttach).not.toHaveBeenCalled();
  });

  it("snapshot without attach fails", async () => {
    const result = await runHostOp({
      op: "host_gui_snapshot",
      args: { conversation_id: CID },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("HostGuiNotAttached");
    }
  });

  it("attach then snapshot keeps refs desktop-side", async () => {
    runGuiAttach.mockResolvedValue({
      ok: true,
      hwnd: 42,
      pid: 99,
      title: "记事本",
      last_agent_tick: 10,
    });
    runGuiAct.mockResolvedValue({
      ok: true,
      title: "记事本",
      snapshot_version: 1,
      controls: '- button "确定" [ref=e1]',
      refs: { e1: "42,1,1" },
      jpeg_b64: "abc",
    });
    const attached = await runHostOp({
      op: "host_gui_attach",
      args: { conversation_id: CID },
    });
    expect(attached.ok).toBe(true);
    if (attached.ok) {
      expect(attached.value.title).toBe("记事本");
    }
    const snap = await runHostOp({
      op: "host_gui_snapshot",
      args: { conversation_id: CID },
    });
    expect(snap.ok).toBe(true);
    if (snap.ok) {
      expect(snap.value.controls).toContain("ref=e1");
      expect(snap.value.refs).toBeUndefined();
      expect(snap.value.jpeg_b64).toBe("abc");
    }
  });

  it("click stale ref fails without invoking UIA", async () => {
    runGuiAttach.mockResolvedValue({
      ok: true,
      hwnd: 42,
      pid: 99,
      title: "W",
      last_agent_tick: 1,
    });
    await runHostOp({
      op: "host_gui_attach",
      args: { conversation_id: CID },
    });
    const result = await runHostOp({
      op: "host_gui_click",
      args: { conversation_id: CID, ref: "e9" },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("HostGuiRefStale");
    }
    expect(runGuiAct).not.toHaveBeenCalled();
  });

  it("click after snapshot forwards runtime id", async () => {
    runGuiAttach.mockResolvedValue({
      ok: true,
      hwnd: 42,
      pid: 99,
      title: "W",
      last_agent_tick: 1,
    });
    runGuiAct
      .mockResolvedValueOnce({
        ok: true,
        title: "W",
        snapshot_version: 1,
        controls: '- button "确定" [ref=e1]',
        refs: { e1: "42,1,1" },
      })
      .mockResolvedValueOnce({
        ok: true,
        title: "W",
        control_name: "确定",
        clicked: { was_disabled: false, matched: true, name: "确定" },
      });
    await runHostOp({
      op: "host_gui_attach",
      args: { conversation_id: CID },
    });
    await runHostOp({
      op: "host_gui_snapshot",
      args: { conversation_id: CID },
    });
    const clicked = await runHostOp({
      op: "host_gui_click",
      args: { conversation_id: CID, ref: "e1", snapshot_version: 1 },
    });
    expect(clicked.ok).toBe(true);
    expect(runGuiAct).toHaveBeenLastCalledWith(
      expect.objectContaining({
        op: "click",
        hwnd: 42,
        ref: "e1",
        runtime_id: "42,1,1",
      }),
    );
  });

  it("password deny and user abort map kinds", async () => {
    runGuiAttach.mockResolvedValue({
      ok: true,
      hwnd: 42,
      pid: 99,
      title: "W",
      last_agent_tick: 1,
    });
    runGuiAct
      .mockResolvedValueOnce({
        ok: true,
        title: "W",
        snapshot_version: 1,
        controls: '- edit "密码" [ref=e1] password=true',
        refs: { e1: "1,2" },
      })
      .mockResolvedValueOnce({
        ok: false,
        kind: "HostGuiPasswordDenied",
        detail: "密码框。",
      });
    await runHostOp({
      op: "host_gui_attach",
      args: { conversation_id: CID },
    });
    await runHostOp({
      op: "host_gui_snapshot",
      args: { conversation_id: CID },
    });
    const typed = await runHostOp({
      op: "host_gui_type",
      args: { conversation_id: CID, ref: "e1", text: "x" },
    });
    expect(typed.ok).toBe(false);
    if (!typed.ok) {
      expect(typed.error.kind).toBe("HostGuiPasswordDenied");
    }
  });

  it("user abort on click clears the session", async () => {
    runGuiAttach.mockResolvedValue({
      ok: true,
      hwnd: 42,
      pid: 99,
      title: "W",
      last_agent_tick: 1,
    });
    runGuiAct
      .mockResolvedValueOnce({
        ok: true,
        title: "W",
        snapshot_version: 1,
        controls: '- button "确定" [ref=e1]',
        refs: { e1: "42,1,1" },
      })
      .mockResolvedValueOnce({
        ok: false,
        kind: "HostGuiUserAbort",
        detail: "用户在该窗口里动了键鼠。",
      });
    await runHostOp({
      op: "host_gui_attach",
      args: { conversation_id: CID },
    });
    await runHostOp({
      op: "host_gui_snapshot",
      args: { conversation_id: CID },
    });
    const first = await runHostOp({
      op: "host_gui_click",
      args: { conversation_id: CID, ref: "e1" },
    });
    expect(first.ok).toBe(false);
    if (!first.ok) {
      expect(first.error.kind).toBe("HostGuiUserAbort");
    }
    const again = await runHostOp({
      op: "host_gui_snapshot",
      args: { conversation_id: CID },
    });
    expect(again.ok).toBe(false);
    if (!again.ok) {
      expect(again.error.kind).toBe("HostGuiNotAttached");
    }
  });
});
