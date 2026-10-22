import type { GrantSessionWellKnown } from "@shared/ipc-contract";

/** Optional hints forwarded to `fs:grantSessionReadonlyRoot`. */
export type GrantFolderHints = {
  /** Absolute local directory (C1-wide mount path transport). */
  path?: string;
  wellKnown?: GrantSessionWellKnown;
  targetName?: string;
  /** Existing session root id — upgrade without a path. */
  rootId?: string;
  /** Open the OS folder picker (input-area「添加文件夹」). */
  pick?: boolean;
};

export function grantIpcFields(hints?: GrantFolderHints): {
  path?: string;
  wellKnown?: GrantSessionWellKnown;
  targetName?: string;
  rootId?: string;
  pick?: boolean;
} {
  if (!hints) return {};
  return {
    ...(hints.path ? { path: hints.path } : {}),
    ...(hints.wellKnown ? { wellKnown: hints.wellKnown } : {}),
    ...(hints.targetName ? { targetName: hints.targetName } : {}),
    ...(hints.rootId ? { rootId: hints.rootId } : {}),
    ...(hints.pick ? { pick: true } : {}),
  };
}
