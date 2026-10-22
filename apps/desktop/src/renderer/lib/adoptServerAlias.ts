import { hasLocalFiles } from "@/lib/capabilities";

/**
 * Store the alias from a grant registration receipt onto the desktop session root.
 *
 * `external/<alias>/` is the server's namespace — it mints the alias when it
 * records the grant, and the model addresses the mount by that name. The desktop
 * root is minted without one, so this is not a correction of a local guess but
 * the only place the alias is ever written.
 *
 * Returns whether the alias actually landed. It is not optional: the local
 * engine resolves `external/<alias>/` against exactly this row, and a root that
 * kept no alias is a mount nothing can open.
 */
export async function adoptServerAlias(
  conversationId: string,
  rootId: string,
  alias: string,
  workingRoot?: boolean,
): Promise<boolean> {
  if (!alias) return false;
  if (!hasLocalFiles() || !window.fsApi?.adoptSessionRootAlias) return false;
  try {
    return await window.fsApi.adoptSessionRootAlias(
      conversationId,
      rootId,
      alias,
      workingRoot,
    );
  } catch {
    return false;
  }
}
