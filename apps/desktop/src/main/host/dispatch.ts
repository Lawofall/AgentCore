import type { HostOpInput, HostOpResult } from "@shared/host-contract";
import { hostApps } from "./apps";
import { listAudioDevices, setDefaultAudio } from "./audio";
import { hostInfo, hostPing } from "./info";
import { hostOsLogSummary } from "./logs";
import { hostNetworkSummary } from "./network";
import { clampPackageTimeout, hostPackageInstall } from "./package";
import { hostPower } from "./power";
import { err } from "./result";
import { restartService } from "./service";
import { openSettings } from "./settings";
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
    case "host_audio_devices":
      return listAudioDevices();
    case "host_storage":
      return hostStorage();
    case "host_power":
      return hostPower();
    case "host_network_summary":
      return hostNetworkSummary();
    case "host_apps":
      return hostApps();
    case "host_os_log_summary":
      return hostOsLogSummary(args);
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
    case "host_open_settings": {
      const panel = String(args.panel ?? "")
        .trim()
        .toLowerCase();
      if (!panel) return err("panel is required");
      return openSettings(panel);
    }
    case "host_audio_set_default": {
      const deviceId = String(args.device_id ?? "").trim();
      const deviceName = String(args.device_name ?? "").trim();
      return setDefaultAudio(deviceId, deviceName);
    }
    case "host_service_restart": {
      const service = String(args.service ?? "").trim();
      return restartService(service);
    }
    case "host_package_install": {
      const manager = String(args.manager ?? "").trim();
      const packageId = String(args.package_id ?? "").trim();
      const cask = args.cask === true;
      const timeoutSeconds = clampPackageTimeout(args.timeout_seconds);
      return hostPackageInstall(manager, packageId, timeoutSeconds, cask);
    }
    default:
      return err(`unknown host op: ${op}`);
  }
}
