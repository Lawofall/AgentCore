/** Per-conversation attached window for Computer Use (desktop-side only). */

export type HostGuiSession = {
  hwnd: number;
  pid: number;
  title: string;
  snapshotVersion: number;
  /** snapshot ref → UIA RuntimeId joined with commas */
  refs: Record<string, string>;
  lastAgentTick: number;
  aborted: boolean;
};

const sessions = new Map<string, HostGuiSession>();

export function guiSessionOf(
  conversationId: string,
): HostGuiSession | undefined {
  const id = conversationId.trim();
  if (!id) return undefined;
  return sessions.get(id);
}

export function setGuiSession(
  conversationId: string,
  session: HostGuiSession,
): void {
  const id = conversationId.trim();
  if (!id) return;
  sessions.set(id, session);
}

export function clearGuiSession(conversationId: string): void {
  const id = conversationId.trim();
  if (!id) return;
  sessions.delete(id);
}

export function markGuiAborted(conversationId: string): void {
  const session = guiSessionOf(conversationId);
  if (session) session.aborted = true;
}
