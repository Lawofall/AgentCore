import {
  useActiveMessageHasVisibleText,
  useConversationStore,
  usePrecedingUserMessageId,
} from "@/stores/conversation";
import type { Execution } from "@/stores/execution";
import {
  type EndpointKind,
  sidePanelFocusTabId,
  useSidePanelStore,
} from "@/stores/sidePanel";
import { useCallback, useMemo } from "react";
import { INPUT_ID, isEndpointId } from "./constants";
import { resolveCaptainSinkId } from "./helpers";

export interface GraphDrillHandoff {
  interactive: boolean;
  messageId: string | null;
  onNodeSelect?: (runId: string) => void;
  onEndpointSelect?: (
    contentMessageId: string,
    title: string,
    endpoint: EndpointKind,
  ) => void;
  onClose?: () => void;
}

/** Drill-in / highlight contract for GraphView and its hosts. */
export function useGraphDrillIn(
  execution: Execution | null,
  {
    interactive,
    messageId,
    onNodeSelect,
    onEndpointSelect,
    onClose,
  }: GraphDrillHandoff,
) {
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const focusMessage = useConversationStore((s) => s.focusMessage);
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const taskMessageId = usePrecedingUserMessageId(messageId);
  const hasAnswerText = useActiveMessageHasVisibleText(messageId);
  const finalAnswerId = hasAnswerText ? messageId : null;

  const showRunDetailHere = useCallback(
    (runId: string) => {
      if (!messageId) return;
      const run = execution?.runs.find((r) => r.id === runId);
      const role = execution?.agents.find((a) => a.id === run?.agentId)?.role;
      showRunDetail(messageId, runId, role);
    },
    [execution, messageId, showRunDetail],
  );

  // Highlight follows focusSurface (dock active OR a float) — closing the dock
  // must not clear a floating run's lit node (UX §十).
  const litRunId = useSidePanelStore((s) => {
    const focusId = sidePanelFocusTabId(s);
    const active = s.tabs.find((t) => t.id === focusId);
    return active?.kind === "run" && active.messageId === messageId
      ? active.runId
      : null;
  });

  const litEndpointMessageId = useSidePanelStore((s) => {
    const focusId = sidePanelFocusTabId(s);
    const active = s.tabs.find((t) => t.id === focusId);
    return active?.kind === "content" && active.messageId === messageId
      ? active.contentMessageId
      : null;
  });

  const captainRun = useMemo(() => {
    if (!execution) return null;
    const id = resolveCaptainSinkId(execution.runs);
    return id ? (execution.runs.find((r) => r.id === id) ?? null) : null;
  }, [execution]);

  const activateNode = useCallback(
    (id: string) => {
      if (id === INPUT_ID) {
        if (!taskMessageId) return;
        if (onEndpointSelect) {
          onEndpointSelect(taskMessageId, "提问", "prompt");
          return;
        }
        focusMessage(taskMessageId, conversationId);
        if (interactive) onClose?.();
        return;
      }
      if (captainRun && id === captainRun.id) {
        if (!finalAnswerId) return;
        if (onEndpointSelect) {
          onEndpointSelect(finalAnswerId, "最终回答", "answer");
          return;
        }
        focusMessage(finalAnswerId, conversationId);
        if (interactive) onClose?.();
        return;
      }
      if (isEndpointId(id)) return;
      if (onNodeSelect) {
        onNodeSelect(id);
        return;
      }
      showRunDetailHere(id);
      onClose?.();
    },
    [
      onNodeSelect,
      onEndpointSelect,
      showRunDetailHere,
      finalAnswerId,
      taskMessageId,
      captainRun,
      focusMessage,
      conversationId,
      interactive,
      onClose,
    ],
  );

  return {
    activateNode,
    showRunDetailHere,
    litRunId,
    litEndpointMessageId,
    finalAnswerId,
    taskMessageId,
    captainRun,
  };
}
