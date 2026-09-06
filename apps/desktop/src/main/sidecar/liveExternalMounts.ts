/**
 * Main-only hook: after grant / adopt / revoke, push the session
 * `externalMounts` snapshot to a live sidecar without renderer seeing abs.
 *
 * Sidecar IPC owns the manager instance; fs IPC must not import it (cycle).
 */

type Pusher = (conversationId: string) => Promise<void>;

let pusher: Pusher | null = null;

export function setLiveExternalMountsPusher(fn: Pusher | null): void {
  pusher = fn;
}

export async function pushLiveExternalMounts(
  conversationId: string,
): Promise<void> {
  const cid = conversationId.trim();
  if (!cid || !pusher) return;
  await pusher(cid);
}
