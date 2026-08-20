import { actAuthorizedByLabel } from "@/components/graph/actAuthLabels";
import {
  type Message,
  assistantProjectionId,
  useActiveHasMoreBefore,
  useActiveMessages,
  useConversationStore,
} from "@/stores/conversation";
import { useDisclosureStore } from "@/stores/disclosure";
import type { ActKind } from "@/stores/execution";
import { ArrowUp } from "lucide-react";

/** 「新开一队、接着上一张继续」——跨回合是新图接上文，不是往旧图里加人。 */
export function graphAppendAnchorLabel(
  _actKind?: ActKind | string | null,
): string {
  return "新开一队、接着上一张继续";
}

function findPrevGraphHost(
  messages: readonly Message[],
  prevExecutionId?: string | null,
  hostMessageId?: string | null,
): Message | undefined {
  const prevId =
    typeof prevExecutionId === "string" ? prevExecutionId.trim() : "";
  const hostRef = typeof hostMessageId === "string" ? hostMessageId.trim() : "";

  if (prevId) {
    return messages.find(
      (m) =>
        m.executionId === prevId ||
        m.process?.some((s) => s.kind === "team" && s.execution_id === prevId),
    );
  }
  if (hostRef) {
    return messages.find(
      (m) => m.id === hostRef || m.serverMessageId === hostRef,
    );
  }
  return undefined;
}

/**
 * 协作图续接锚点——渲染在**当前**回合新图上，点击导航到上一张图。
 *
 * - 新路径：`prevExecutionId`（来自 `run_plan.prev_execution_id`）
 * - 旧 journal：`hostMessageId`（来自 `graph_append` process 步）
 * 宿主气泡不在当前消息窗时改为说明、不静默吞点击。
 */
export function GraphAppendAnchor({
  prevExecutionId,
  hostMessageId,
  actKind,
  authorizedBy,
}: {
  prevExecutionId?: string | null;
  hostMessageId?: string | null;
  actKind?: ActKind | string | null;
  authorizedBy?: string | null;
}) {
  const messages = useActiveMessages();
  const hasMoreBefore = useActiveHasMoreBefore();
  const focusMessage = useConversationStore((s) => s.focusMessage);
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const label = graphAppendAnchorLabel(actKind);
  const authLabel = actAuthorizedByLabel(authorizedBy);
  const host = findPrevGraphHost(messages, prevExecutionId, hostMessageId);
  const missingHint = host
    ? null
    : hasMoreBefore
      ? "上一张图不在当前消息窗，往上翻可查看"
      : "上一张图不在当前对话";

  const inner = (
    <>
      <ArrowUp size={14} className="shrink-0" aria-hidden />
      <span className="truncate">
        {label}
        {authLabel ? (
          <span className="text-xs opacity-80"> · {authLabel}</span>
        ) : null}
        {missingHint ? (
          <span className="text-xs opacity-80"> · {missingHint}</span>
        ) : null}
      </span>
    </>
  );

  const chrome =
    "inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-2.5 py-1.5 text-left text-sm text-muted-foreground";

  if (!host) {
    return (
      <div
        data-testid="graph-append-anchor"
        data-unavailable="true"
        role="note"
        title={missingHint ?? undefined}
        className={`${chrome} cursor-default`}
      >
        {inner}
      </div>
    );
  }

  return (
    <button
      type="button"
      data-testid="graph-append-anchor"
      onClick={() => {
        const slotId = assistantProjectionId(host);
        if (conversationId && slotId) {
          // 展开目标内联协作图（默认展开时清掉「已收起」偏离值）。
          useDisclosureStore
            .getState()
            .setKey(`${conversationId}::${slotId}:inline-graph`, true, true);
        }
        focusMessage(host.id, conversationId);
      }}
      className={`${chrome} transition-colors hover:bg-muted hover:text-foreground`}
    >
      {inner}
    </button>
  );
}
