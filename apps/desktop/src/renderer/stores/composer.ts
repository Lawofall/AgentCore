import type {
  PendingAgentMention,
  PendingAttachment,
} from "@/components/chat/message-input/composerAttachments";
import { migrateLegacyDraft } from "@/lib/inlineBody";
import { registerConversationUiClearer, uiGet, uiSet } from "@/lib/uiStorage";
import { useConversationStore } from "@/stores/conversation";
import type { SetStateAction } from "react";
import { create } from "zustand";

/**
 * Per-conversation drafts for the unified turn composer (`TurnComposer` in chat
 * `MessageInput`).
 *
 * Keying the draft (text + pending attachments) by conversation moves it OUT of
 * component state, which buys the two things the old local-state design couldn't do:
 * switching conversations keeps the half-typed order, and the 回填 channel below
 * lands in a real draft even if the composer is briefly unmounted. Entries
 * self-delete once both text and attachments are empty, so the map stays bounded
 * to conversations with a live draft.
 *
 * Persistence: draft TEXT + attachment *metadata* survive an app restart
 * (`uiStorage`, debounced + flushed on unload, capped to the {@link PERSIST_LIMIT}
 * most recent). Binary bytes stay on disk under main-process ``attach-staging``
 * (indexed by ``stagingId``); we never put file bytes into localStorage. Preview
 * ``text`` is truncated for the quota; stale staging is honest at send time.
 *
 * 回填 channel: ask card / run-detail / debate drop text into the ACTIVE conversation's
 * draft via {@link fill}. `append` (the default) adds the text as a new line after any
 * existing draft so a user can stack answers to several questions; `replace` overwrites.
 * `fillToken` is a monotonic focus hint — the mounted composer refocuses its textarea
 * when it changes (the draft text itself arrives through the store subscription).
 */
export interface ComposerDraft {
  value: string;
  attachments: PendingAttachment[];
  /** Pending `@Agent` chips（旁路 attachments）。 */
  agentMentions: PendingAgentMention[];
  /** Last edit (ms epoch) — recency key for the persistence cap. */
  updatedAt: number;
}

const EMPTY_DRAFT: ComposerDraft = {
  value: "",
  attachments: [],
  agentMentions: [],
  updatedAt: 0,
};

const COMPOSER_DRAFTS_KEY = "composer-drafts";
/** Persist at most this many drafts (most recently edited win). */
const PERSIST_LIMIT = 30;
const PERSIST_DEBOUNCE_MS = 300;
/** Cap attachment metadata per draft so ``composer-drafts`` stays bounded. */
const PERSIST_ATTACH_MAX = 8;
/** Cap agent mention chips per draft. */
const PERSIST_AGENT_MAX = 10;
/** Truncate preview text when writing to uiStorage (full preview stays in-memory). */
const PERSIST_ATTACH_TEXT_CAP = 8 * 1024;

/** Draft-conversation (no id yet) drafts live under a fixed sentinel key. */
export function draftKeyFor(conversationId: string | null): string {
  return conversationId ?? "__draft__";
}

function sanitizeAttachment(raw: unknown): PendingAttachment | null {
  if (!raw || typeof raw !== "object") return null;
  const a = raw as Record<string, unknown>;
  if (typeof a.id !== "string" || !a.id) return null;
  if (typeof a.key !== "string" || !a.key) return null;
  if (typeof a.name !== "string" || !a.name) return null;
  if (typeof a.path !== "string") return null;
  if (typeof a.text !== "string") return null;
  if (typeof a.truncated !== "boolean") return null;
  if (a.kind !== "file" && a.kind !== "dir" && a.kind !== "conversation") {
    return null;
  }
  const out: PendingAttachment = {
    id: a.id,
    key: a.key,
    name: a.name,
    path: a.path,
    text: a.text,
    truncated: a.truncated,
    kind: a.kind,
  };
  if (typeof a.conversationId === "string")
    out.conversationId = a.conversationId;
  if (typeof a.workspacePath === "string") out.workspacePath = a.workspacePath;
  if (typeof a.stagingId === "string") out.stagingId = a.stagingId;
  if (typeof a.citedRootId === "string") out.citedRootId = a.citedRootId;
  if (typeof a.citedRelPath === "string") out.citedRelPath = a.citedRelPath;
  if (typeof a.binary === "boolean") out.binary = a.binary;
  return out;
}

function sanitizeAgentMention(raw: unknown): PendingAgentMention | null {
  if (!raw || typeof raw !== "object") return null;
  const a = raw as Record<string, unknown>;
  if (typeof a.id !== "string" || !a.id) return null;
  if (typeof a.agentId !== "string" || !a.agentId) return null;
  if (typeof a.role !== "string" || !a.role) return null;
  return { id: a.id, agentId: a.agentId, role: a.role };
}

function serializeAttachments(
  attachments: PendingAttachment[],
): PendingAttachment[] {
  // File blobs can't survive localStorage — drop unfinished browser drafts.
  return attachments
    .filter((a) => !a.fileBlob || a.workspacePath || a.stagingId)
    .slice(0, PERSIST_ATTACH_MAX)
    .map((a) => {
      const text =
        a.text.length > PERSIST_ATTACH_TEXT_CAP
          ? a.text.slice(0, PERSIST_ATTACH_TEXT_CAP)
          : a.text;
      const out: PendingAttachment = {
        id: a.id,
        key: a.key,
        name: a.name,
        path: a.path,
        text,
        truncated: a.truncated || a.text.length > PERSIST_ATTACH_TEXT_CAP,
        kind: a.kind,
      };
      if (a.conversationId) out.conversationId = a.conversationId;
      if (a.workspacePath) out.workspacePath = a.workspacePath;
      if (a.stagingId) out.stagingId = a.stagingId;
      if (a.citedRootId) out.citedRootId = a.citedRootId;
      if (a.citedRelPath) out.citedRelPath = a.citedRelPath;
      if (a.binary) out.binary = a.binary;
      return out;
    });
}

function draftHasContent(
  d: Pick<ComposerDraft, "value" | "attachments" | "agentMentions">,
): boolean {
  return (
    Boolean(d.value) ||
    (d.attachments?.length ?? 0) > 0 ||
    (d.agentMentions?.length ?? 0) > 0
  );
}

function loadDrafts(): Record<string, ComposerDraft> {
  const parsed = uiGet<Record<string, unknown>>(COMPOSER_DRAFTS_KEY);
  if (!parsed || typeof parsed !== "object") return {};
  const out: Record<string, ComposerDraft> = {};
  for (const [key, entry] of Object.entries(parsed)) {
    if (!entry || typeof entry !== "object") continue;
    const {
      value,
      updatedAt,
      attachments: rawAtts,
      agentMentions: rawAgents,
    } = entry as {
      value?: unknown;
      updatedAt?: unknown;
      attachments?: unknown;
      agentMentions?: unknown;
    };
    const valueStr = typeof value === "string" ? value : "";
    const attachments = Array.isArray(rawAtts)
      ? rawAtts
          .map(sanitizeAttachment)
          .filter((a): a is PendingAttachment => a !== null)
          .slice(0, PERSIST_ATTACH_MAX)
      : [];
    const agentMentions = Array.isArray(rawAgents)
      ? rawAgents
          .map(sanitizeAgentMention)
          .filter((a): a is PendingAgentMention => a !== null)
          .slice(0, PERSIST_AGENT_MAX)
      : [];
    if (!valueStr && attachments.length === 0 && agentMentions.length === 0)
      continue;
    out[key] = {
      value: migrateLegacyDraft(
        valueStr,
        attachments.length,
        agentMentions.length,
      ),
      attachments,
      agentMentions,
      updatedAt: typeof updatedAt === "number" ? updatedAt : 0,
    };
  }
  return out;
}

function persistDrafts(drafts: Record<string, ComposerDraft>): void {
  const entries = Object.entries(drafts)
    .filter(([, d]) => draftHasContent(d))
    .sort(([, a], [, b]) => b.updatedAt - a.updatedAt)
    .slice(0, PERSIST_LIMIT)
    .map(([key, d]) => {
      const payload: {
        value: string;
        updatedAt: number;
        attachments?: PendingAttachment[];
        agentMentions?: PendingAgentMention[];
      } = { value: d.value, updatedAt: d.updatedAt };
      if (d.attachments.length > 0) {
        payload.attachments = serializeAttachments(d.attachments);
      }
      if ((d.agentMentions?.length ?? 0) > 0) {
        payload.agentMentions = d.agentMentions.slice(0, PERSIST_AGENT_MAX);
      }
      return [key, payload] as const;
    });
  if (entries.length === 0) uiSet(COMPOSER_DRAFTS_KEY, undefined);
  else uiSet(COMPOSER_DRAFTS_KEY, Object.fromEntries(entries));
}

function resolve<T>(action: SetStateAction<T>, prev: T): T {
  return typeof action === "function" ? (action as (p: T) => T)(prev) : action;
}

/** Write back a draft, dropping the key when it emptied (bounded map). */
function write(
  drafts: Record<string, ComposerDraft>,
  key: string,
  next: ComposerDraft,
): Record<string, ComposerDraft> {
  const out = { ...drafts };
  if (!draftHasContent(next)) delete out[key];
  else out[key] = next;
  return out;
}

interface ComposerDraftState {
  drafts: Record<string, ComposerDraft>;
  /** Monotonic; bumped on every {@link fill} so the mounted composer refocuses. */
  fillToken: number;
  /**
   * Monotonic; bumped ONLY when a draft promotes to a brand-new conversation on
   * first send ({@link armDockFlip}). The composer dock-flip animation (center →
   * bottom) keys off this instead of the passive centered→bottom transition, so
   * merely SWITCHING to another (already-persisted) conversation never triggers
   * the flight animation — that transition looked like "输入框跳动".
   */
  dockFlipToken: number;
  setValue: (key: string, action: SetStateAction<string>) => void;
  setAttachments: (
    key: string,
    action: SetStateAction<PendingAttachment[]>,
  ) => void;
  setAgentMentions: (
    key: string,
    action: SetStateAction<PendingAgentMention[]>,
  ) => void;
  /**
   * 回填 the active conversation's draft with `text` (default: append as a new line).
   */
  fill: (text: string, mode?: "append" | "replace") => void;
  /** Arm the one-shot center→bottom dock-flip for the imminent first-send promote. */
  armDockFlip: () => void;
}

export const useComposerDraftStore = create<ComposerDraftState>((set) => ({
  drafts: loadDrafts(),
  fillToken: 0,
  dockFlipToken: 0,
  setValue: (key, action) =>
    set((s) => {
      const prev = s.drafts[key] ?? EMPTY_DRAFT;
      return {
        drafts: write(s.drafts, key, {
          ...prev,
          value: resolve(action, prev.value),
          updatedAt: Date.now(),
        }),
      };
    }),
  setAttachments: (key, action) =>
    set((s) => {
      const prev = s.drafts[key] ?? EMPTY_DRAFT;
      return {
        drafts: write(s.drafts, key, {
          ...prev,
          agentMentions: prev.agentMentions ?? [],
          attachments: resolve(action, prev.attachments ?? []),
          updatedAt: Date.now(),
        }),
      };
    }),
  setAgentMentions: (key, action) =>
    set((s) => {
      const prev = s.drafts[key] ?? EMPTY_DRAFT;
      return {
        drafts: write(s.drafts, key, {
          ...prev,
          attachments: prev.attachments ?? [],
          agentMentions: resolve(action, prev.agentMentions ?? []),
          updatedAt: Date.now(),
        }),
      };
    }),
  fill: (text, mode = "append") => {
    const conversationId =
      useConversationStore.getState().currentConversationId;
    set((s) => {
      const key = draftKeyFor(conversationId);
      const prev = s.drafts[key] ?? EMPTY_DRAFT;
      const value =
        mode === "append" && prev.value.trim()
          ? `${prev.value}\n${text}`
          : text;
      return {
        drafts: write(s.drafts, key, { ...prev, value, updatedAt: Date.now() }),
        fillToken: s.fillToken + 1,
      };
    });
  },
  armDockFlip: () => set((s) => ({ dockFlipToken: s.dockFlipToken + 1 })),
}));

// Debounced persistence: setValue fires per keystroke, so batch writes; flush on
// unload so the last keystrokes before closing the app aren't lost to the debounce.
let persistTimer: ReturnType<typeof setTimeout> | null = null;
let lastPersisted: Record<string, ComposerDraft> | null = null;

function flushPersist(): void {
  if (persistTimer !== null) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  const drafts = useComposerDraftStore.getState().drafts;
  if (drafts === lastPersisted) return;
  lastPersisted = drafts;
  persistDrafts(drafts);
}

useComposerDraftStore.subscribe((s, prev) => {
  if (s.drafts === prev.drafts) return;
  if (persistTimer !== null) clearTimeout(persistTimer);
  persistTimer = setTimeout(flushPersist, PERSIST_DEBOUNCE_MS);
});

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", flushPersist);
}

registerConversationUiClearer((conversationId) => {
  const key = draftKeyFor(conversationId);
  const drafts = useComposerDraftStore.getState().drafts;
  if (!(key in drafts)) return;
  const next = { ...drafts };
  delete next[key];
  useComposerDraftStore.setState({ drafts: next });
  persistDrafts(next);
  lastPersisted = next;
});

/**
 * Send-failure rollback: hand the draft back after the optimistic bubble was
 * already shown and the composer cleared (attachment upload failed at the last
 * step). Merges instead of clobbering — the user may have started typing the
 * next message during the wait — and targets the CURRENT draft key, which is the
 * new conversation's when a draft had just been promoted.
 */
export function restoreComposerDraft(
  conversationId: string | null,
  draft: {
    value: string;
    attachments: PendingAttachment[];
    agentMentions: PendingAgentMention[];
  },
): void {
  const key = draftKeyFor(conversationId);
  const store = useComposerDraftStore.getState();
  if (draft.value) {
    store.setValue(key, (prev) =>
      prev.trim() ? `${draft.value}\n${prev}` : draft.value,
    );
  }
  if (draft.attachments.length > 0) {
    store.setAttachments(key, (prev) => [
      ...draft.attachments,
      ...prev.filter((p) => !draft.attachments.some((a) => a.id === p.id)),
    ]);
  }
  if (draft.agentMentions.length > 0) {
    store.setAgentMentions(key, (prev) => [
      ...draft.agentMentions,
      ...prev.filter((p) => !draft.agentMentions.some((a) => a.id === p.id)),
    ]);
  }
}

/**
 * Every ``stagingId`` still referenced by a draft — the survivor set for the
 * main-process ``attach-staging`` sweep. Read after {@link loadDrafts}, so it
 * already reflects what survived the {@link PERSIST_LIMIT} eviction.
 */
export function liveStagingIds(): string[] {
  const ids = new Set<string>();
  for (const draft of Object.values(useComposerDraftStore.getState().drafts)) {
    for (const att of draft.attachments ?? []) {
      if (att.stagingId) ids.add(att.stagingId);
    }
  }
  return [...ids];
}

/** @internal vitest — reload drafts from uiStorage without `vi.resetModules` (hangs on the conversation graph). */
export function __reloadComposerDraftsForTests(): void {
  if (persistTimer !== null) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  lastPersisted = null;
  useComposerDraftStore.setState({
    drafts: loadDrafts(),
    fillToken: 0,
    dockFlipToken: 0,
  });
}
