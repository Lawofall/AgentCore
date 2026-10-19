/**
 * 本机 Host 能力 IPC 契约 —— 主进程 / preload / renderer 三端共享。
 *
 * 服务端经 `host_op_required` ClientTool 回填；桌面主进程履行探测和短命令，
 * 禁止云 API 进程直探 127.0.0.1。
 */

export const HOST_CHANNELS = {
  runOp: "host:runOp",
} as const;

export type HostOpName =
  | "host_ping"
  | "host_info"
  | "host_storage"
  | "host_power"
  | "host_network_summary"
  | "host_apps"
  | "host_shell"
  | "host_gui_attach"
  | "host_gui_snapshot"
  | "host_gui_click"
  | "host_gui_type";

export interface HostOpInput {
  op: HostOpName | string;
  args?: Record<string, unknown>;
}

export type HostOpResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: { kind: string; detail: string } };

export interface HostApi {
  runOp: (input: HostOpInput) => Promise<HostOpResult>;
}
