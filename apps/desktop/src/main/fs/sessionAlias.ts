import { ensureReady, getRoot, saveSessionGrants, setRoot } from "./roots";

/**
 * Store the alias the server issued for one conversation session grant.
 *
 * The server owns `external/<alias>/`: it derives the alias from the label with
 * its own rules (non-ASCII folds to a base32 digest, collisions get a suffix)
 * and that is what the model and the UI address. A freshly minted root has no
 * alias at all until this lands — startTurn / resume / mid-turn
 * `updateExternalMounts` snapshot `externalMounts` off the stored alias, so
 * anything other than the server's own answer makes every op on that mount
 * fail with PathNotFound under the local engine.
 *
 * Conversation ownership is re-checked here so a compromised renderer cannot
 * re-alias another conversation's mount.
 */
export async function adoptSessionRootAlias(
  conversationId: string,
  rootId: string,
  alias: string,
): Promise<boolean> {
  await ensureReady();
  const root = getRoot(rootId);
  if (!root?.sessionOnly || root.conversationId !== conversationId) {
    return false;
  }
  const next = alias.trim();
  if (!next) return false;
  if (root.alias === next) return true;
  setRoot({ ...root, alias: next });
  await saveSessionGrants();
  return true;
}
