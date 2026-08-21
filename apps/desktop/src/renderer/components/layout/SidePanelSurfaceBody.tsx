import { ApprovalPrompt } from "@/components/chat/ApprovalPrompt";
import { Markdown } from "@/components/chat/Markdown";
import { RunDetailScroll } from "@/components/chat/detail/RunDetailScroll";
import { FileTabSurface } from "@/components/layout/FileTabSurface";
import { ConversationChangesPanel } from "@/components/workspace/ConversationChangesPanel";
import { WorkspaceMode } from "@/components/workspace/WorkspacePanel";
import {
  useActiveMessageContent,
  useConversationStore,
} from "@/stores/conversation";
import { usePendingApprovals } from "@/stores/interactions";
import {
  CHANGES_TAB_ID,
  type DetailTab,
  WORKSPACE_TAB_ID,
  useSidePanelStore,
} from "@/stores/sidePanel";
import type { ReactNode } from "react";

/**
 * Tab body shared by the docked SidePanel and in-app floats (UX §十).
 * Keep chrome-free so the same surface renders in either host.
 */
export function SidePanelSurfaceBody({
  tabId,
  /** Float hosts surface pending tool approvals in-panel (not only chat bottom bar). */
  showApprovals = false,
}: {
  tabId: string;
  showApprovals?: boolean;
}) {
  const tabs = useSidePanelStore((s) => s.tabs);
  const closeTab = useSidePanelStore((s) => s.closeTab);

  const tab =
    tabId === WORKSPACE_TAB_ID || tabId === CHANGES_TAB_ID
      ? null
      : (tabs.find((t) => t.id === tabId) ?? null);

  const contentMessageId =
    tab?.kind === "content" ? tab.contentMessageId : null;
  const contentTabText = useActiveMessageContent(contentMessageId);
  const simplePromptId =
    tab?.kind === "simple-turn" ? tab.promptMessageId : null;
  const simpleAnswerId =
    tab?.kind === "simple-turn" ? tab.answerMessageId : null;
  const simplePromptText = useActiveMessageContent(simplePromptId);
  const simpleAnswerText = useActiveMessageContent(simpleAnswerId);

  let body: ReactNode = null;

  if (tabId === WORKSPACE_TAB_ID) {
    body = <WorkspaceMode />;
  } else if (tabId === CHANGES_TAB_ID) {
    body = <ConversationChangesPanel />;
  } else if (tab?.kind === "run") {
    body = (
      <RunDetailScroll
        key={`${tab.id}:${tab.runId}`}
        messageId={tab.messageId}
        runId={tab.runId}
      />
    );
  } else if (tab?.kind === "file") {
    body = (
      <FileTabSurface
        path={tab.path}
        name={tab.name}
        workspaceId={tab.workspaceId}
        channel={tab.channel}
        onClose={() => closeTab(tab.id)}
      />
    );
  } else if (tab?.kind === "content") {
    body = (
      <div className="absolute inset-0 overflow-y-auto p-4">
        <Markdown content={contentTabText} />
      </div>
    );
  } else if (tab?.kind === "simple-turn") {
    body = (
      <div className="absolute inset-0 overflow-y-auto p-4">
        <section className="space-y-2">
          <h3 className="text-xs font-medium text-muted-foreground">提问</h3>
          <Markdown content={simplePromptText || "（无提问）"} />
        </section>
        <section className="mt-6 space-y-2 border-t border-border pt-6">
          <h3 className="text-xs font-medium text-muted-foreground">回答</h3>
          <Markdown
            content={
              simpleAnswerText ||
              (simpleAnswerId ? "（生成中…）" : "（无回答）")
            }
          />
        </section>
      </div>
    );
  }

  if (!showApprovals) return body;

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="relative min-h-0 flex-1 overflow-hidden">{body}</div>
      <FloatApprovalStrip />
    </div>
  );
}

function FloatApprovalStrip() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const pending = usePendingApprovals(conversationId);
  if (pending.length === 0) return null;
  return (
    <div className="shrink-0 border-t border-border bg-card">
      <ApprovalPrompt />
    </div>
  );
}

/** Title for a float chrome from store tab id. */
export function sidePanelFloatTitle(
  tabId: string,
  tabs: readonly DetailTab[],
): string {
  if (tabId === WORKSPACE_TAB_ID) return "工作区";
  if (tabId === CHANGES_TAB_ID) return "改动";
  return tabs.find((t) => t.id === tabId)?.title ?? "浮窗";
}
