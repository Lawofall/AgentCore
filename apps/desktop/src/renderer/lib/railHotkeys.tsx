import { getConversations, useConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import {
  buildWorkspaceGroups,
  foldersForConversationRail,
  useSharedWithMeWorkspaceGroups,
  useWorkspaceGroups,
} from "@/hooks/useWorkspaceGroups";
import { isMac } from "@/lib/platform";
import {
  conversationAtRailHotkey,
  listVisibleRailConversations,
  railHotkeySlots,
} from "@/lib/sidebarRailVisibility";
import { isSharedWithMeFolder } from "@/services/folders";
import {
  aiAttentionEntries,
  useRequiredConversationIds,
} from "@/stores/aiAttention";
import { useConversationStore } from "@/stores/conversation";
import { useSidebarStore } from "@/stores/sidebar";
import {
  type ReactNode,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { NavigateFunction } from "react-router-dom";

const EMPTY_SLOTS = new Map<string, number>();

const RailHotkeySlotsContext = createContext<Map<string, number>>(EMPTY_SLOTS);
const RailHotkeyHintVisibleContext = createContext(false);

/**
 * True when the platform-mod chord for rail 1–9 is armed (Ctrl on Win/Linux,
 * Cmd on Mac) and Shift/Alt are not also down. Drives the row-tail digit hint.
 */
export function isRailHotkeyHintModifier(
  e: Pick<KeyboardEvent, "altKey" | "shiftKey" | "ctrlKey" | "metaKey">,
  mac = isMac,
): boolean {
  if (e.altKey || e.shiftKey) return false;
  return mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}

/** Window-level subscribe used by {@link RailHotkeySlotsProvider}. */
export function subscribeRailHotkeyHint(
  onChange: (visible: boolean) => void,
): () => void {
  const sync = (e: KeyboardEvent) => onChange(isRailHotkeyHintModifier(e));
  const clear = () => onChange(false);
  const onVisibility = () => {
    if (document.hidden) clear();
  };
  window.addEventListener("keydown", sync);
  window.addEventListener("keyup", sync);
  window.addEventListener("blur", clear);
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    window.removeEventListener("keydown", sync);
    window.removeEventListener("keyup", sync);
    window.removeEventListener("blur", clear);
    document.removeEventListener("visibilitychange", onVisibility);
  };
}

function requiredIdsSnapshot(): ReadonlySet<string> {
  const ids = new Set<string>();
  for (const e of aiAttentionEntries()) ids.add(e.conversationId);
  return ids;
}

/** Imperative twin of the rail Provider — the global shortcut handler is not React. */
export function snapshotVisibleRailConversations() {
  const conversations = getConversations();
  const allFolders = getFolders();
  const requiredIds = requiredIdsSnapshot();
  const { folderGroupOrder, expandedSections } = useSidebarStore.getState();
  const currentId = useConversationStore.getState().currentConversationId;
  const listed = foldersForConversationRail(allFolders);
  return listVisibleRailConversations({
    conversations,
    ownedGroups: buildWorkspaceGroups(
      conversations,
      listed.filter((f) => !isSharedWithMeFolder(f)),
      requiredIds,
      { folderGroupOrder },
    ),
    sharedGroups: buildWorkspaceGroups(
      conversations,
      listed.filter(isSharedWithMeFolder),
      requiredIds,
      { includeEmpty: true, uncapped: true },
    ),
    expandedSections,
    currentId,
    requiredIds,
  });
}

/**
 * Open the Nth visible rail conversation. Returns false when that slot is empty
 * so the keydown handler can leave the chord to the host (browser tabs).
 */
export function switchRailConversationByDigit(
  digit: string,
  navigate: NavigateFunction,
): boolean {
  const conv = conversationAtRailHotkey(
    digit,
    snapshotVisibleRailConversations(),
  );
  if (!conv) return false;
  useConversationStore.getState().switchConversation(conv.id);
  navigate(`/conversations/${conv.id}`);
  return true;
}

/**
 * Supplies 1–9 indices for the wide sidebar list. Narrow drawer does not wrap
 * this — its visual order can differ, and badges would lie.
 */
export function RailHotkeySlotsProvider({ children }: { children: ReactNode }) {
  const conversations = useConversations();
  const ownedGroups = useWorkspaceGroups();
  const sharedGroups = useSharedWithMeWorkspaceGroups();
  const expandedSections = useSidebarStore((s) => s.expandedSections);
  const currentId = useConversationStore((s) => s.currentConversationId);
  const requiredIds = useRequiredConversationIds();
  const [hintVisible, setHintVisible] = useState(false);
  const slots = useMemo(
    () =>
      railHotkeySlots(
        listVisibleRailConversations({
          conversations,
          ownedGroups,
          sharedGroups,
          expandedSections,
          currentId,
          requiredIds,
        }),
      ),
    [
      conversations,
      ownedGroups,
      sharedGroups,
      expandedSections,
      currentId,
      requiredIds,
    ],
  );
  useEffect(() => subscribeRailHotkeyHint(setHintVisible), []);
  return (
    <RailHotkeyHintVisibleContext.Provider value={hintVisible}>
      <RailHotkeySlotsContext.Provider value={slots}>
        {children}
      </RailHotkeySlotsContext.Provider>
    </RailHotkeyHintVisibleContext.Provider>
  );
}

export function useRailHotkeyIndex(conversationId: string): number | undefined {
  return useContext(RailHotkeySlotsContext).get(conversationId);
}

/** Digit hints stay hidden until the platform-mod key is held. */
export function useRailHotkeyHintVisible(): boolean {
  return useContext(RailHotkeyHintVisibleContext);
}
