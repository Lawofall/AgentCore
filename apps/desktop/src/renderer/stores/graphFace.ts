import { create } from "zustand";

export type ConversationFace = "text" | "graph";

interface GraphFaceState {
  byKey: Record<string, ConversationFace>;
  toggle: (key: string) => void;
  /** 换一场回到正文。同一场里点按钮不会走到这里。 */
  reset: (key: string) => void;
}

export const useGraphFaceStore = create<GraphFaceState>((set) => ({
  byKey: {},
  toggle: (key) =>
    set((state) => ({
      byKey: {
        ...state.byKey,
        [key]: state.byKey[key] === "graph" ? "text" : "graph",
      },
    })),
  reset: (key) =>
    set((state) => {
      if (state.byKey[key] !== "graph") return state;
      return { byKey: { ...state.byKey, [key]: "text" } };
    }),
}));
