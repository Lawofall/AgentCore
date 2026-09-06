import { pickAndGrantSessionFolder } from "@/lib/grantOrganizeFolder";
import {
  type GrantFolderHints,
  pickAndGrantReadonlyFolder,
} from "@/lib/grantReadonlyFolder";
import { fulfillClientToolOnce } from "@/services/clientToolFulfill";
import type { InteractionSettleOrigin } from "@/services/interaction";
import type { ExternalMountRequiredPayload } from "@/types/events";
import type { GrantSessionWellKnown } from "@shared/ipc-contract";

type MountMode = "readonly" | "organize" | "attach_rw";

/**
 * Desktop half of the external-mount client-tool channel.
 *
 * After the server suspends and streams ``external_mount_required``,
 * resolve path / well_known+target_name / root_id (no picker), POST
 * ``external-grants``, and settle. Read-only is silent; organize / attach_rw
 * confirm in main (system dialog).
 */
export async function performExternalMount(
  payload: ExternalMountRequiredPayload,
  conversationId: string,
  origin: InteractionSettleOrigin,
): Promise<void> {
  await fulfillClientToolOnce({
    requestId: payload.request_id,
    conversationId,
    origin,
    logLabel: "externalMountOps",
    perform: async () => runExternalMount(payload, conversationId),
  });
}

type ClientToolResult =
  | {
      ok: true;
      value: {
        root_id: string;
        alias: string;
        label: string;
        display_label?: string;
        namespace: string;
      };
    }
  | {
      ok: false;
      error: { kind: string; detail: string; reason?: string };
    };

const WELL_KNOWN = new Set<GrantSessionWellKnown>([
  "desktop",
  "downloads",
  "documents",
]);

function hintsFromPayload(
  payload: ExternalMountRequiredPayload,
): GrantFolderHints | undefined {
  const path =
    typeof payload.path === "string" && payload.path.trim()
      ? payload.path.trim()
      : undefined;
  const wellKnown = WELL_KNOWN.has(payload.well_known as GrantSessionWellKnown)
    ? (payload.well_known as GrantSessionWellKnown)
    : undefined;
  const targetName =
    typeof payload.target_name === "string" && payload.target_name.trim()
      ? payload.target_name.trim()
      : undefined;
  const rootId =
    typeof payload.root_id === "string" && payload.root_id.trim()
      ? payload.root_id.trim()
      : undefined;
  if (!path && !wellKnown && !targetName && !rootId) return undefined;
  return {
    ...(path ? { path } : {}),
    ...(wellKnown ? { wellKnown } : {}),
    ...(targetName ? { targetName } : {}),
    ...(rootId ? { rootId } : {}),
  };
}

function mountMode(payload: ExternalMountRequiredPayload): MountMode {
  return payload.mode === "organize" || payload.mode === "attach_rw"
    ? payload.mode
    : "readonly";
}

async function runExternalMount(
  payload: ExternalMountRequiredPayload,
  conversationId: string,
): Promise<ClientToolResult> {
  const mode = mountMode(payload);
  const hints = hintsFromPayload(payload);
  const result =
    mode === "readonly"
      ? await pickAndGrantReadonlyFolder(conversationId, hints)
      : await pickAndGrantSessionFolder(conversationId, mode, hints);
  if (!result.ok) {
    if (result.reason === "unavailable") {
      return {
        ok: false,
        error: {
          kind: "ExternalMountError",
          detail: "非桌面环境，无法挂载本机目录",
          reason: "unavailable",
        },
      };
    }
    return {
      ok: false,
      error: {
        kind: "ExternalMountError",
        detail: result.message,
        // Keep structured grant/IPC reason (not_found / not_directory / cancelled / …).
        reason: result.reason,
      },
    };
  }
  return {
    ok: true,
    value: {
      root_id: result.root.id,
      alias: result.alias,
      label: result.root.name,
      ...(result.displayLabel ? { display_label: result.displayLabel } : {}),
      namespace: result.namespace,
    },
  };
}
