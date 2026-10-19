/**
 * Computer Use GUI ops — Windows UI Automation via PowerShell.
 * First knife: win32 only. Empty tree / unattached / password / user abort fail honestly.
 */

import type { HostOpResult } from "@shared/host-contract";
import { Notification } from "electron";
import {
  clearGuiSession,
  guiSessionOf,
  markGuiAborted,
  setGuiSession,
} from "./gui-session";
import { runGuiAct, runGuiAttach } from "./gui-uia";
import { err, ok } from "./result";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function conversationIdOf(args: Record<string, unknown>): string {
  return typeof args.conversation_id === "string"
    ? args.conversation_id.trim()
    : "";
}

function notifyOperating(title: string): void {
  try {
    if (Notification.isSupported()) {
      new Notification({
        title: "AgentCore",
        body: `正在操作「${title}」`,
        silent: true,
      }).show();
    }
  } catch {
    // Indicator must never fail the op.
  }
}

function failFromScript(result: Record<string, unknown>): HostOpResult {
  const kind =
    typeof result.kind === "string" && result.kind.trim()
      ? result.kind.trim()
      : "HostOpError";
  const detail =
    typeof result.detail === "string" && result.detail.trim()
      ? result.detail.trim()
      : "本机窗口操作失败。";
  return err(detail, kind);
}

function applyTick(
  conversationId: string,
  result: Record<string, unknown>,
): void {
  const session = guiSessionOf(conversationId);
  if (!session) return;
  const tick = result.last_agent_tick;
  if (typeof tick === "number" && Number.isFinite(tick)) {
    session.lastAgentTick = tick;
  }
}

export async function hostGui(
  op: string,
  args: Record<string, unknown>,
): Promise<HostOpResult> {
  if (process.platform !== "win32") {
    return err("本机窗口操作第一刀仅 Windows。", "HostGuiUnsupportedPlatform");
  }
  const conversationId = conversationIdOf(args);
  if (!conversationId) {
    return err("缺少 conversation_id。", "HostOpError");
  }

  if (op === "host_gui_attach") {
    const picked = await runGuiAttach(process.pid);
    if (picked.ok === false) return failFromScript(picked);
    const hwnd = Number(picked.hwnd);
    const pid = Number(picked.pid);
    const title = String(picked.title ?? "").trim() || "窗口";
    if (!Number.isFinite(hwnd) || hwnd === 0) {
      return err("选窗结果无效。", "HostOpError");
    }
    const tick = Number(picked.last_agent_tick) || 0;
    setGuiSession(conversationId, {
      hwnd,
      pid: Number.isFinite(pid) ? pid : 0,
      title,
      snapshotVersion: 0,
      refs: {},
      lastAgentTick: tick,
      aborted: false,
    });
    notifyOperating(title);
    return ok({
      ok: true,
      title,
      snapshot_version: 0,
      last_agent_tick: tick,
    });
  }

  const session = guiSessionOf(conversationId);
  if (!session) {
    return err("还没有附着窗口。", "HostGuiNotAttached");
  }
  if (session.aborted) {
    return err("本轮窗口操作已停止。", "HostGuiUserAbort");
  }

  if (op === "host_gui_snapshot") {
    const result = await runGuiAct({
      op: "snapshot",
      hwnd: session.hwnd,
      snapshot_version: session.snapshotVersion,
      last_agent_tick: session.lastAgentTick,
    });
    if (result.ok === false) {
      if (result.kind === "HostGuiUserAbort") markGuiAborted(conversationId);
      return failFromScript(result);
    }
    const refsRaw = asRecord(result.refs);
    const refs: Record<string, string> = {};
    for (const [key, value] of Object.entries(refsRaw)) {
      if (typeof value === "string" && value.trim()) refs[key] = value.trim();
    }
    session.refs = refs;
    const version = Number(result.snapshot_version);
    session.snapshotVersion = Number.isFinite(version)
      ? version
      : session.snapshotVersion + 1;
    session.title = String(result.title ?? session.title);
    applyTick(conversationId, result);
    const { refs: _drop, ...rest } = result;
    return ok({ ...rest, snapshot_version: session.snapshotVersion });
  }

  if (op === "host_gui_click" || op === "host_gui_type") {
    const ref = typeof args.ref === "string" ? args.ref.trim() : "";
    if (!ref) return err("缺少 ref。", "HostOpError");
    const runtimeId = session.refs[ref];
    if (!runtimeId) {
      return err("控件 ref 已过期或找不到。", "HostGuiRefStale");
    }
    const requestedVersion = args.snapshot_version;
    if (
      requestedVersion !== undefined &&
      Number(requestedVersion) !== session.snapshotVersion
    ) {
      return err("控件 ref 已过期或找不到。", "HostGuiRefStale");
    }
    notifyOperating(session.title);
    const payload: Record<string, unknown> = {
      op: op === "host_gui_click" ? "click" : "type",
      hwnd: session.hwnd,
      ref,
      runtime_id: runtimeId,
      last_agent_tick: session.lastAgentTick,
    };
    if (op === "host_gui_type") payload.text = String(args.text ?? "");
    const result = await runGuiAct(payload);
    if (result.ok === false) {
      if (result.kind === "HostGuiUserAbort") {
        markGuiAborted(conversationId);
        clearGuiSession(conversationId);
      }
      return failFromScript(result);
    }
    applyTick(conversationId, result);
    return ok(result);
  }

  return err(`unknown host op: ${op}`);
}
