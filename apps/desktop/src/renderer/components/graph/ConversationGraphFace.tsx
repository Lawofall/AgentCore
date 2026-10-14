import { GraphView } from "@/components/graph/GraphView";
import { foldConversationGraphExecutions } from "@/components/graph/conversationGraph";
import { useChatPaneId } from "@/lib/chatPane";
import {
  ensureFullMessageRuns,
  loadNewerMessages,
  loadOlderMessages,
} from "@/services/messages";
import {
  useActiveHasMoreAfter,
  useActiveHasMoreBefore,
  useActiveLoadingNewer,
  useActiveLoadingOlder,
  useActiveMessages,
} from "@/stores/conversation";
import {
  isDebate,
  projectRuntime,
  useExecutionStore,
} from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

const PAGE_CAP = 40;

/**
 * The conversation's single graph. Loads the resident window out to both
 * edges, folds every non-debate segment, and draws that one picture.
 */
export function ConversationGraphFace() {
  const conversationId = useChatPaneId();
  const messages = useActiveMessages();
  const hasMoreBefore = useActiveHasMoreBefore();
  const hasMoreAfter = useActiveHasMoreAfter();
  const loadingOlder = useActiveLoadingOlder();
  const loadingNewer = useActiveLoadingNewer();
  const olderPages = useRef(0);
  const newerPages = useRef(0);

  useEffect(() => {
    olderPages.current = 0;
    newerPages.current = 0;
  }, [conversationId]);

  useEffect(() => {
    if (!conversationId || !hasMoreBefore || loadingOlder) return;
    if (olderPages.current >= PAGE_CAP) return;
    olderPages.current += 1;
    void loadOlderMessages(conversationId);
  }, [conversationId, hasMoreBefore, loadingOlder]);

  useEffect(() => {
    if (!conversationId || !hasMoreAfter || loadingNewer) return;
    if (newerPages.current >= PAGE_CAP) return;
    newerPages.current += 1;
    void loadNewerMessages(conversationId);
  }, [conversationId, hasMoreAfter, loadingNewer]);

  useEffect(() => {
    if (!conversationId) return;
    const hydrate = useExecutionStore.getState().hydrateFromJournal;
    for (const message of messages) {
      if (message.role !== "assistant" || !message.runs) continue;
      if (message.runs.eventsComplete === false) {
        void ensureFullMessageRuns(
          conversationId,
          message.serverMessageId ?? message.id,
        );
        continue;
      }
      const slot = useExecutionStore.getState().byId[message.id];
      if (!slot?.plan) hydrate(message.id, message.runs);
    }
  }, [conversationId, messages]);

  const stamp = useExecutionStore((s) => {
    let key = "";
    for (const message of messages) {
      if (message.role !== "assistant") continue;
      const runtime = s.byId[message.id];
      const execution = runtime ? projectRuntime(runtime) : null;
      if (!execution || isDebate(execution)) continue;
      for (const run of execution.runs) {
        key += `${run.id}:${run.status}:${run.dependsOn.join(".")};`;
      }
      key += "|";
    }
    return key;
  });

  const folded = useMemo(() => {
    const byId = useExecutionStore.getState().byId;
    return foldConversationGraphExecutions(
      messages
        .filter((message) => message.role === "assistant")
        .map((message) => {
          const runtime = byId[message.id];
          return {
            messageId: message.id,
            execution: runtime ? projectRuntime(runtime) : null,
          };
        }),
    );
    // stamp is the content key; messages covers turns that have no runtime yet.
  }, [stamp, messages]);

  const filling = hasMoreBefore || hasMoreAfter || loadingOlder || loadingNewer;
  const execution = folded.execution;

  if (!execution) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        {filling ? (
          <Loader2 size={16} className="animate-spin text-muted-foreground" />
        ) : (
          <p className="text-sm text-muted-foreground">这张图还没有节点</p>
        )}
      </div>
    );
  }

  return (
    <div className="relative h-full min-h-0 w-full">
      <GraphView
        interactive
        fitMode="view"
        pinnedExecution={execution}
        onNodeSelect={(runId) => {
          const owner = folded.owners.get(runId);
          if (!owner) return;
          const run = execution.runs.find((item) => item.id === runId);
          useSidePanelStore
            .getState()
            .showRunDetail(owner, runId, run?.role ?? undefined);
        }}
      />
    </div>
  );
}
