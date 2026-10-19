import type { HostOpInput, HostOpResult } from "@shared/host-contract";
import { hostApps } from "./apps";
import { hostGui } from "./gui";
import { hostInfo, hostPing } from "./info";
import { hostNetworkSummary } from "./network";
import { hostPower } from "./power";
import { err } from "./result";
import { clampShellTimeout, hostShell } from "./shell";
import { hostStorage } from "./storage";

export async function runHostOp(input: HostOpInput): Promise<HostOpResult> {
  const op = String(input.op || "").trim();
  const args = input.args ?? {};
  switch (op) {
    case "host_ping":
      return hostPing();
    case "host_info":
      return hostInfo();
    case "host_storage":
      return hostStorage();
    case "host_power":
      return hostPower();
    case "host_network_summary":
      return hostNetworkSummary();
    case "host_apps":
      return hostApps();
    case "host_shell": {
      const command = String(args.command ?? "");
      const timeoutSeconds = clampShellTimeout(args.timeout_seconds);
      return hostShell(command, timeoutSeconds, {
        cwd: typeof args.cwd === "string" ? args.cwd : undefined,
        conversationId:
          typeof args.conversation_id === "string"
            ? args.conversation_id
            : undefined,
        rootId: typeof args.root_id === "string" ? args.root_id : undefined,
      });
    }
    case "host_gui_attach":
    case "host_gui_snapshot":
    case "host_gui_click":
    case "host_gui_type":
      return hostGui(op, args);
    default:
      return err(`unknown host op: ${op}`);
  }
}
