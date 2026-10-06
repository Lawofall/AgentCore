import { api } from "@/services/api";

/** One row on the assembly page or the conversation editor. */
export interface ToolSwitchRow {
  id: string;
  label: string;
  summary: string;
  doc_tool: string;
  off: boolean;
  note: string | null;
  /** Model-facing names this switch removes. Absent on older payloads. */
  tools?: string[];
}

export interface ToolSwitchboard {
  disabled: string[];
  switches: ToolSwitchRow[];
}

type Listener = () => void;

let accountBoard: ToolSwitchboard | null = null;
const listeners = new Set<Listener>();

/** Blank composer only. Null means「还没改过」, create then copies the account list. */
let composerDraftDisabled: string[] | null = null;
const draftListeners = new Set<Listener>();

function publish(board: ToolSwitchboard): void {
  accountBoard = board;
  for (const listener of listeners) listener();
}

export function subscribeAccountToolSwitches(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAccountToolSwitchboard(): ToolSwitchboard | null {
  return accountBoard;
}

function publishDraft(): void {
  for (const listener of draftListeners) listener();
}

/** Remember the deny list for the conversation that does not exist yet. */
export function setComposerDraftDisabledTools(
  ids: readonly string[] | null,
): void {
  if (ids == null) {
    if (composerDraftDisabled == null) return;
    composerDraftDisabled = null;
  } else {
    composerDraftDisabled = [...ids];
  }
  publishDraft();
}

/** Stable snapshot for `useSyncExternalStore`. */
export function getComposerDraftDisabledTools(): string[] | null {
  return composerDraftDisabled;
}

/** Drop cached boards. Tests only. */
export function __resetToolSwitchStoresForTests(): void {
  accountBoard = null;
  composerDraftDisabled = null;
}

/** Copy for the create POST. Null means omit the field. */
export function peekComposerDraftDisabledTools(): string[] | null {
  return composerDraftDisabled == null ? null : [...composerDraftDisabled];
}

export function subscribeComposerDraftDisabledTools(
  listener: Listener,
): () => void {
  draftListeners.add(listener);
  return () => draftListeners.delete(listener);
}

export async function loadAccountToolSwitches(): Promise<ToolSwitchboard> {
  const board = await api.get<ToolSwitchboard>("/v1/users/me/tool-switches");
  publish(board);
  return board;
}

export async function saveAccountToolSwitches(
  disabled: string[],
): Promise<ToolSwitchboard> {
  const board = await api.put<ToolSwitchboard>("/v1/users/me/tool-switches", {
    disabled,
  });
  publish(board);
  return board;
}

export async function loadConversationToolSwitches(
  conversationId: string,
): Promise<ToolSwitchboard> {
  return api.get<ToolSwitchboard>(
    `/v1/conversations/${conversationId}/tool-switches`,
  );
}

export async function saveConversationToolSwitches(
  conversationId: string,
  disabled: string[],
): Promise<ToolSwitchboard> {
  return api.put<ToolSwitchboard>(
    `/v1/conversations/${conversationId}/tool-switches`,
    { disabled },
  );
}

/** Next deny list after flipping one switch. `on` means the tool stays available. */
export function disabledAfterToggle(
  disabled: readonly string[],
  id: string,
  on: boolean,
): string[] {
  const next = new Set(disabled);
  if (on) next.delete(id);
  else next.add(id);
  return [...next];
}
