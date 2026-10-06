import {
  type ConversationSplit,
  type PaneId,
  closeSplitPane,
  openBeside,
  promoteDraftPane,
} from "@/lib/conversationSplit";
import { useConversationStore } from "@/stores/conversation/store";
import { useConversationSplitStore } from "@/stores/conversationSplit";
import type { NavigateFunction } from "react-router-dom";

function goToPane(id: PaneId, navigate: NavigateFunction): void {
  if (id) {
    useConversationStore.getState().switchConversation(id);
    navigate(`/conversations/${id}`);
    return;
  }
  useConversationStore.getState().switchConversation(null);
  navigate("/");
}

/** 右键 / 行菜单：挂到焦点栏旁边，焦点留在原来那场。 */
export function openConversationBeside(
  targetId: string,
  navigate: NavigateFunction,
): void {
  const current = useConversationStore.getState().currentConversationId;
  const { split, setSplit } = useConversationSplitStore.getState();
  const next = openBeside(split, current, targetId);
  if (!next || next === split) return;
  setSplit(next);
  useConversationStore.getState().ensureResidentSlice(targetId);
  const focused = next.panes[next.focus];
  if (focused !== current) {
    goToPane(focused, navigate);
    return;
  }
  const path = window.location.hash.replace(/^#/, "") || "/";
  const onChat = path === "/" || /^\/conversations\/[^/]+$/.test(path);
  if (!onChat) goToPane(current, navigate);
}

export function focusConversationPane(
  index: 0 | 1,
  navigate: NavigateFunction,
): void {
  const { split, roomFits, setSplit } = useConversationSplitStore.getState();
  if (!split || !roomFits || split.focus === index) return;
  setSplit({ ...split, focus: index });
  goToPane(split.panes[index], navigate);
}

export function focusOtherConversationPane(
  navigate: NavigateFunction,
): boolean {
  const { split, roomFits } = useConversationSplitStore.getState();
  if (!split || !roomFits) return false;
  focusConversationPane((1 - split.focus) as 0 | 1, navigate);
  return true;
}

export function closeConversationPane(
  index: 0 | 1,
  navigate: NavigateFunction,
): void {
  const { split, setSplit } = useConversationSplitStore.getState();
  if (!split) return;
  const keep = closeSplitPane(split, index);
  const closedFocus = split.focus === index;
  setSplit(null);
  if (closedFocus) goToPane(keep, navigate);
}

/** 草稿首发落成之后、跳进新路由之前，把草稿栏换成这场。 */
export function promoteDraftConversationPane(conversationId: string): void {
  const { split, setSplit } = useConversationSplitStore.getState();
  const next = promoteDraftPane(split, conversationId);
  if (next && next !== split) setSplit(next);
}

export function applySplitRatio(ratio: number): void {
  const { split, setSplit } = useConversationSplitStore.getState();
  if (!split || split.ratio === ratio) return;
  setSplit({ ...split, ratio });
}

export type { ConversationSplit };
