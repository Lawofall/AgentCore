import { createZustandUiStorage } from "@/lib/uiStorage";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

const uiPersistStorage = createJSONStorage(() => createZustandUiStorage());

/** Expanded-rail width bounds (px). Floor equals default — only widen. */
export const SIDEBAR_MIN_WIDTH = 240;
export const SIDEBAR_DEFAULT_WIDTH = 240;
export const SIDEBAR_MAX_WIDTH = 400;
/** Collapsed icon rail — fixed, not part of the drag range. */
export const SIDEBAR_COLLAPSED_WIDTH = 56;

export function clampSidebarWidth(w: number): number {
  return Math.max(
    SIDEBAR_MIN_WIDTH,
    Math.min(SIDEBAR_MAX_WIDTH, Math.round(w)),
  );
}

function uniqIds(ids: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/**
 * Persist a user-dragged visible folder-group order.
 * Empty `stored` → first pin: write the visible list as-is.
 * Otherwise fill stored's visible slots with `nextVisibleIds` in order and keep
 * ids that are not on the rail (overflow / empty folders) in their 序位.
 * Visible ids that were not in `stored` append after those slots.
 */
export function mergeFolderGroupOrder(
  stored: readonly string[],
  nextVisibleIds: readonly string[],
): string[] {
  const visible = uniqIds(nextVisibleIds);
  const prev = uniqIds(stored);
  if (prev.length === 0) return visible;
  const visibleSet = new Set(visible);
  const result: string[] = [];
  let visIdx = 0;
  for (const id of prev) {
    if (visibleSet.has(id)) {
      const next = visible[visIdx];
      if (next !== undefined) {
        result.push(next);
        visIdx += 1;
      }
    } else {
      result.push(id);
    }
  }
  while (visIdx < visible.length) {
    const next = visible[visIdx];
    if (next === undefined) break;
    result.push(next);
    visIdx += 1;
  }
  return result;
}

interface SidebarState {
  collapsed: boolean;
  /** Expanded-rail width in px, clamped to [240, 400] (persisted). */
  width: number;
  /** True while the user is dragging the resize handle (session-only; not persisted). */
  resizing: boolean;
  /** Per-section expand state, keyed by section id. Workspace groups key on their
   * `folderId`; an absent key means "no explicit user choice yet" (the view then
   * applies its own default — see `WorkspaceGroups`). */
  expandedSections: Record<string, boolean>;
  /**
   * Manual folder-group order on the rail. Empty = still sort by latest activity.
   * Once the user reorders, ids are pinned; activity no longer jumps groups.
   * Hidden ids (overflow / empty folders) stay in place across later drags.
   */
  folderGroupOrder: string[];

  toggleCollapsed: () => void;
  setCollapsed: (collapsed: boolean) => void;
  setWidth: (width: number) => void;
  setResizing: (resizing: boolean) => void;
  /** Double-click resize handle → restore default width. */
  resetWidth: () => void;
  toggleSection: (sectionId: string) => void;
  /** Explicitly set a section's expand state. Preferred over `toggleSection` where
   * the displayed default differs from the stored value (e.g. an auto-expanded
   * active group) — clicking must flip what the user *sees*, not the absent key. */
  setSection: (sectionId: string, expanded: boolean) => void;
  /** Pin the visible folder-group permutation. First call writes `nextVisibleIds`;
   * later calls splice that permutation into the stored list. */
  reorderFolderGroups: (nextVisibleIds: string[]) => void;
}

export const useSidebarStore = create<SidebarState>()(
  persist(
    (set) => ({
      collapsed: false,
      width: SIDEBAR_DEFAULT_WIDTH,
      resizing: false,
      expandedSections: {},
      folderGroupOrder: [],

      toggleCollapsed: () => set((s) => ({ collapsed: !s.collapsed })),
      setCollapsed: (collapsed) => set({ collapsed }),
      setWidth: (width) => set({ width: clampSidebarWidth(width) }),
      setResizing: (resizing) => set({ resizing }),
      resetWidth: () => set({ width: SIDEBAR_DEFAULT_WIDTH }),
      toggleSection: (sectionId) =>
        set((s) => ({
          expandedSections: {
            ...s.expandedSections,
            [sectionId]: !s.expandedSections[sectionId],
          },
        })),
      setSection: (sectionId, expanded) =>
        set((s) => ({
          expandedSections: { ...s.expandedSections, [sectionId]: expanded },
        })),
      reorderFolderGroups: (nextVisibleIds) =>
        set((s) => ({
          folderGroupOrder: mergeFolderGroupOrder(
            s.folderGroupOrder,
            nextVisibleIds,
          ),
        })),
    }),
    {
      name: "sidebar",
      storage: uiPersistStorage,
      // Persist only view prefs (rail collapse + width + per-workspace expand +
      // pinned folder-group order) so layout survives restarts; methods / the
      // ephemeral drag flag aren't serialized.
      partialize: (s) => ({
        collapsed: s.collapsed,
        width: s.width,
        expandedSections: s.expandedSections,
        folderGroupOrder: s.folderGroupOrder,
      }),
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        state.width = clampSidebarWidth(
          typeof state.width === "number" && Number.isFinite(state.width)
            ? state.width
            : SIDEBAR_DEFAULT_WIDTH,
        );
        state.folderGroupOrder = Array.isArray(state.folderGroupOrder)
          ? uniqIds(
              state.folderGroupOrder.filter((id) => typeof id === "string"),
            )
          : [];
      },
    },
  ),
);
