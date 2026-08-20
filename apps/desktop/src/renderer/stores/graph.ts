import { createZustandUiStorage } from "@/lib/uiStorage";
import type { GraphEdge, GraphLayout } from "@agentcore/graph-layout";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type { GraphEdge, GraphLayout };

const LAYOUTS: GraphLayout[] = ["tree", "leftright"];

const uiPersistStorage = createJSONStorage(() => createZustandUiStorage());

// Per-graph layout (ELK positions + structural edges) is NOT global state: with
// §9.3 every multi-agent message renders its own inline graph, so the layout is
// view state owned locally by each {@link GraphView}. Only the *choice* of layout
// algorithm is global — it is a user preference that applies to every graph and
// persists across sessions.
interface GraphState {
  /** Active layout algorithm — a shared, persisted user preference. */
  layoutKind: GraphLayout;
  setLayoutKind: (kind: GraphLayout) => void;
  /** Phase 2: always show audit-confirmed inject edges on the collaboration graph. */
  showAuditInjectFlow: boolean;
  setShowAuditInjectFlow: (on: boolean) => void;
}

export const useGraphStore = create<GraphState>()(
  persist(
    (set) => ({
      layoutKind: "leftright",
      showAuditInjectFlow: false,

      setLayoutKind: (layoutKind) => {
        if (!(LAYOUTS as string[]).includes(layoutKind)) return;
        set({ layoutKind });
      },

      setShowAuditInjectFlow: (showAuditInjectFlow) => {
        set({ showAuditInjectFlow });
      },
    }),
    {
      name: "graph",
      storage: uiPersistStorage,
      version: 1,
      partialize: (s) => ({
        layoutKind: s.layoutKind,
        showAuditInjectFlow: s.showAuditInjectFlow,
      }),
      migrate: (persisted) => {
        const p = (persisted ?? {}) as {
          layoutKind?: GraphLayout;
          showAuditInjectFlow?: boolean;
        };
        return {
          layoutKind:
            p.layoutKind && (LAYOUTS as string[]).includes(p.layoutKind)
              ? p.layoutKind
              : "leftright",
          showAuditInjectFlow: p.showAuditInjectFlow === true,
        };
      },
    },
  ),
);
