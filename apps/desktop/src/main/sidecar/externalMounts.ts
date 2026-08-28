import type { StoredRoot } from "../fs/roots";

/** One `external/<alias>/` mount handed to the local engine for a turn. */
export interface SidecarExternalMount {
  alias: string;
  rootId: string;
  label: string;
  absPath: string;
  mode: "readonly" | "organize" | "attach_rw";
}

/**
 * Session grants → the `externalMounts` snapshot sent with startTurn / resume.
 *
 * `alias` must be the server-issued one (written back when the grant was
 * registered): the model addresses `external/<alias>/` from the server's grant
 * list, and the engine resolves it against exactly this map — a desktop-local
 * guess here comes back as PathNotFound.
 */
export function buildExternalMounts(
  sessionRoots: StoredRoot[],
): SidecarExternalMount[] {
  return sessionRoots
    .filter((r) => r.alias && r.absPath)
    .map((r) => ({
      alias: r.alias as string,
      rootId: r.id,
      label: r.name,
      absPath: r.absPath,
      mode:
        r.mode === "organize" || r.mode === "attach_rw" ? r.mode : "readonly",
    }));
}
