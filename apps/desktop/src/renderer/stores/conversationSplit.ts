import {
  type ConversationSplit,
  parseConversationSplit,
} from "@/lib/conversationSplit";
import { uiGet, uiSet } from "@/lib/uiStorage";
import { DRAFT_KEY } from "@/stores/conversation/runtime";
import { create } from "zustand";

const STORAGE_KEY = "conversation-split";

interface ConversationSplitState {
  split: ConversationSplit | null;
  /** 主列放得下两道还能打字的栏。窄屏或主列太窄时为 false，分屏不画出来。 */
  roomFits: boolean;
  setSplit: (split: ConversationSplit | null) => void;
  setRoomFits: (roomFits: boolean) => void;
}

function readSplit(): ConversationSplit | null {
  return parseConversationSplit(uiGet<unknown>(STORAGE_KEY));
}

export const useConversationSplitStore = create<ConversationSplitState>(
  (set) => ({
    split: readSplit(),
    roomFits: typeof window !== "undefined" ? window.innerWidth >= 1280 : false,
    setSplit: (split) => {
      uiSet(STORAGE_KEY, split ?? undefined);
      set({ split });
    },
    setRoomFits: (roomFits) => set({ roomFits }),
  }),
);

/** 切对话时 LRU 不能丢掉正挂在主列上的切片，包括空着还在加载的那一栏。 */
export function protectedSliceKeys(): string[] {
  const split = useConversationSplitStore.getState().split;
  if (!split) return [];
  return split.panes.map((pane) => pane ?? DRAFT_KEY);
}
