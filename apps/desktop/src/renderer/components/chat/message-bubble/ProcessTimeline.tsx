import { GraphAppendAnchor } from "@/components/chat/GraphAppendAnchor";
import { InlineTeamGraph } from "@/components/chat/InlineTeamGraph";
import { InterjectionTimeline } from "@/components/chat/InterjectionTimeline";
import { Markdown } from "@/components/chat/Markdown";
import {
  ComposingToolLine,
  ToolLine,
  ToolLineGroup,
} from "@/components/chat/ToolLine";
import {
  kickoffReleasedFromPreviews,
  teamGraphVisible,
} from "@/components/chat/debatePreviewPlacement";
import { executionGraphCapabilities } from "@/components/graph/planCapabilities";
import {
  type TimelineNode,
  groupToolRuns,
  reworkChipLabel,
  timelineNodeKeys,
} from "@/lib/processTimeline";
import type {
  CheckpointDisplay,
  PlanReviewDisplay,
  TeamPreviewDisplay,
} from "@/stores/conversation";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import { type ExecutionJournal, useMessageExecution } from "@/stores/execution";
import { renderTimelineInteractionCard } from "@/stores/interactions/registryUi";
import type {
  Citation,
  ProcessStep,
  TurnEvidenceLedgerEntry,
} from "@/types/events";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment } from "react";
import { ThinkingDots, ThinkingHeader } from "./Thinking";

/** Thought 折叠覆盖面：推理/工具 + 弱式决策痕迹（批准/委派授权/推进卡）。
 * 强交互卡（checkpoint/ask/escalation/…）仍外置可见。 */
function isProcessNode(node: TimelineNode): boolean {
  return (
    node.kind === "reasoning" ||
    node.kind === "tool" ||
    node.kind === "tool-group" ||
    node.kind === "approval" ||
    node.kind === "stage_card"
  );
}

function countProcessStats(nodes: TimelineNode[]) {
  let reasoningCount = 0;
  let toolCount = 0;
  for (const node of nodes) {
    if (node.kind === "reasoning") reasoningCount++;
    else if (node.kind === "tool") toolCount++;
    else if (node.kind === "tool-group") toolCount += node.tools.length;
  }
  return { reasoningCount, toolCount };
}

function formatProcessSummary(
  reasoningCount: number,
  toolCount: number,
): string {
  const parts: string[] = [];
  if (reasoningCount > 0) {
    parts.push(
      `Thought ${reasoningCount} step${reasoningCount === 1 ? "" : "s"}`,
    );
  }
  if (toolCount > 0) {
    parts.push(`Used ${toolCount} tool${toolCount === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function InlineReasoning({
  text,
  streaming,
  persistKey,
}: {
  text: string;
  streaming: boolean;
  /** 持久化键（`${messageId}:reason:${i}`）：给了才把「思考过程开合」跨卸载/刷新记住；缺省走会话态。 */
  persistKey?: string | null;
}) {
  // 「直播中自动展开、收场后按保存值」（Q3）；settled 默认收起（S4 不主动摊开多段）。
  const [expanded, toggle] = useStreamAwareDisclosure(
    persistKey ?? null,
    streaming,
    { settledDefault: false },
  );

  return (
    <div className="process-thought min-w-0 max-w-full">
      <ThinkingHeader
        isStreaming={streaming}
        expanded={expanded}
        streamingLabel="Thinking…"
        doneLabel="Thought"
        onToggle={toggle}
      />
      {expanded && (
        <div className="mt-1.5 min-w-0 max-w-full text-muted-foreground">
          <Markdown content={text} isStreaming={streaming} muted />
        </div>
      )}
    </div>
  );
}

function ProcessRow({
  step,
  streaming,
  reworkLabel,
  citations,
  citationToDisplay,
  knownLedgerIds,
  evidenceLedger,
  turnKey,
  rowKey,
  conversationId,
}: {
  step: ProcessStep;
  streaming: boolean;
  /** Presentational copy for `kind===rework` (in-progress vs done). */
  reworkLabel?: string;
  citations: Citation[];
  citationToDisplay?: ReadonlyMap<number, number>;
  knownLedgerIds?: ReadonlySet<string> | null;
  evidenceLedger?: readonly TurnEvidenceLedgerEntry[] | null;
  /** 回合作用域（= messageId）：给了才持久化本行的折叠态；缺省走会话态。 */
  turnKey?: string;
  /** 本行的稳定标识（{@link timelineNodeKeys}）——标记中段插入不再位移它。 */
  rowKey: string;
  /** 所属对话（= conversationId）：仅 browser 单步结果用它懒加载关键帧。 */
  conversationId?: string | null;
}) {
  if (step.kind === "reasoning") {
    return (
      <InlineReasoning
        text={step.text}
        streaming={streaming}
        persistKey={turnKey ? `${turnKey}:reason:${rowKey}` : null}
      />
    );
  }
  if (step.kind === "content") {
    // 旁白/正文：前景色（vs Thought 的 muted）+ 时间线 space-y，不用左边线区分（S4）。
    return (
      <div className="process-narration min-w-0 max-w-full text-foreground">
        <Markdown
          content={step.text}
          citations={citations}
          citationToDisplay={citationToDisplay}
          knownLedgerIds={knownLedgerIds}
          evidenceLedger={evidenceLedger}
          isStreaming={streaming}
        />
      </div>
    );
  }
  if (step.kind === "rework") {
    return (
      <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground">
        {reworkLabel ?? reworkChipLabel(false, true)}
      </span>
    );
  }
  // Positional markers (team/checkpoint/ask/plan_review) are resolved in the timeline
  // map, never routed here — only a `tool` step reaches this tail.
  if (step.kind === "tool")
    return (
      <ToolLine step={step} turnKey={turnKey} conversationId={conversationId} />
    );
  return null;
}

/**
 * In-stream fallback: generic Thinking… when the tail has no live node.
 * Live chrome = running/wait tool, streaming reasoning/content, in-progress rework,
 * composing tool, visible collaboration graph (StatusStrip) at the tail, or a
 * pending user gate. Markers are not live by themselves — delegate/debate omit
 * tool steps (isMarkerStandinTool) and stand in as `team` / interaction markers.
 */
/** Markers that anchor the collaboration graph slot. `graph_append` only paints an
 * anchor label, so its liveness is the graph it belongs to — same gate as `team`. */
export function graphSlotExecutionId(
  step: ProcessStep | undefined,
): string | null {
  if (step?.kind === "team" || step?.kind === "graph_append")
    return step.execution_id;
  return null;
}

export function shouldShowThinkingTail(args: {
  isStreaming: boolean;
  composingTool: boolean;
  last: ProcessStep | undefined;
  graphVisibleAtTail: boolean;
  pendingUserGate: boolean;
}): boolean {
  if (!args.isStreaming || args.composingTool) return false;
  if (args.pendingUserGate || args.graphVisibleAtTail) return false;
  const last = args.last;
  if (!last) return true;
  if (last.kind === "reasoning" || last.kind === "content") return false;
  if (last.kind === "rework") return false;
  if (last.kind === "tool") {
    if (last.tool_name === "wait") return false;
    return last.status !== "running";
  }
  return true;
}

export function ProcessTimeline({
  process,
  isStreaming,
  citations,
  citationToDisplay,
  knownLedgerIds = null,
  evidenceLedger = null,
  composingTool,
  fallbackContent,
  messageId,
  journal,
  conversationId,
  checkpoints,
  planReviews,
  teamPreviews,
  /** When false, never collapse reasoning/tool rows into a summary (run-detail panel).
   * Default true keeps CEO bubble chrome. */
  collapseProcessSteps = true,
}: {
  process: ProcessStep[];
  isStreaming: boolean;
  citations: Citation[];
  citationToDisplay?: ReadonlyMap<number, number>;
  knownLedgerIds?: ReadonlySet<string> | null;
  evidenceLedger?: readonly TurnEvidenceLedgerEntry[] | null;
  composingTool: { toolName: string; chars: number } | null;
  fallbackContent: string;
  messageId?: string;
  journal?: ExecutionJournal;
  conversationId: string | null;
  checkpoints: CheckpointDisplay[];
  planReviews: PlanReviewDisplay[];
  teamPreviews: TeamPreviewDisplay[];
  collapseProcessSteps?: boolean;
}) {
  const execution = useMessageExecution(messageId ?? null);
  const last = process[process.length - 1];
  const hasContentStep = process.some((s) => s.kind === "content");
  const kickoffReleased = kickoffReleasedFromPreviews(teamPreviews);
  const graphVisibleAtTail = (() => {
    const slotExecutionId = graphSlotExecutionId(last);
    if (!slotExecutionId) return false;
    if (!execution || execution.id !== slotExecutionId) return false;
    if (!executionGraphCapabilities(execution).showsTeamGraph) return false;
    // Same gate as InlineTeamGraph: empty teamPreviews is still a provided list.
    return teamGraphVisible(execution.runs, teamPreviews);
  })();
  const pendingUserGate =
    teamPreviews.some((p) => p.status === "pending") ||
    checkpoints.some((c) => c.status === "pending") ||
    planReviews.some((p) => p.status === "pending");
  // wait 结束后不刷 Thinking 尾迹（S4）；下一轮有真实动作再出现。wait / wait-idle
  // reasoning 行本身仍展示（CEO 气泡与 run 详情同源 process）。
  const showThinkingTail = shouldShowThinkingTail({
    isStreaming,
    composingTool: Boolean(composingTool),
    last,
    graphVisibleAtTail,
    pendingUserGate,
  });

  // 摘要步数与可见行同源，避免「Thought 10」展开只剩 3 行。
  // collapseProcessSteps 只控制折叠 chrome，不再 omit wait。
  const nodes = groupToolRuns(process);
  // 稳定 key（时间线一期）：insertBeforeTeam 中段插入不再位移后续行的 React key。
  const nodeKeys = timelineNodeKeys(nodes);

  const { reasoningCount, toolCount } = countProcessStats(nodes);
  // 仅有弱痕迹、无推理/工具时不折叠（避免空摘要按钮）；单段纯 Thought 也不折。
  const shouldCollapseProcess =
    collapseProcessSteps &&
    !isStreaming &&
    (reasoningCount > 0 || toolCount > 0) &&
    !(reasoningCount === 1 && toolCount === 0);
  const [processExpanded, toggleProcess] = useStreamAwareDisclosure(
    messageId ? `${messageId}:process` : null,
    isStreaming,
    { settledDefault: false },
  );
  const processSummary = formatProcessSummary(reasoningCount, toolCount);

  // 协作图应在 CEO 回复下方: when prose only exists as fallbackContent (no content
  // step), slot it before the first team/graph_append marker — never after the
  // whole timeline (that put the graph above the CEO lead-in).
  const fallbackBeforeTeamIdx =
    !hasContentStep && fallbackContent
      ? nodes.findIndex((n) => n.kind === "team" || n.kind === "graph_append")
      : -1;
  const showFallbackAfter =
    !hasContentStep && Boolean(fallbackContent) && fallbackBeforeTeamIdx < 0;

  const renderFallback = (key: string) => (
    <div
      key={key}
      className="process-narration min-w-0 max-w-full text-foreground"
    >
      <Markdown
        content={fallbackContent}
        citations={citations}
        citationToDisplay={citationToDisplay}
        knownLedgerIds={knownLedgerIds}
        evidenceLedger={evidenceLedger}
        isStreaming={isStreaming}
      />
    </div>
  );

  const renderNode = (node: TimelineNode, i: number) => {
    const live = isStreaming && i === nodes.length - 1;
    const nodeKey = nodeKeys[i];
    if (node.kind === "team") {
      return messageId ? (
        <InlineTeamGraph
          key={nodeKey}
          messageId={messageId}
          executionId={node.execution_id}
          journal={journal}
          kickoffReleased={kickoffReleased}
          teamPreviews={teamPreviews}
        />
      ) : null;
    }
    if (node.kind === "graph_append") {
      const append = node as typeof node & {
        act_kind?: string;
        authorized_by?: string;
      };
      return (
        <GraphAppendAnchor
          key={nodeKey}
          hostMessageId={node.host_message_id}
          actKind={append.act_kind}
          authorizedBy={append.authorized_by}
        />
      );
    }
    if (node.kind === "user_interjection") {
      return messageId ? (
        <InterjectionTimeline
          key={nodeKey}
          messageId={messageId}
          interjectionId={node.interjection_id}
        />
      ) : null;
    }
    if (
      node.kind === "checkpoint" ||
      node.kind === "plan_review" ||
      node.kind === "team_preview" ||
      node.kind === "escalation" ||
      node.kind === "approval" ||
      node.kind === "stage_card"
    ) {
      const card = renderTimelineInteractionCard(
        node.kind,
        node,
        {
          checkpoints,
          planReviews,
          teamPreviews,
        },
        {
          messageId: messageId ?? "",
          conversationId,
          interactive: isStreaming,
        },
      );
      if (!card) return null;
      return <div key={nodeKey}>{card}</div>;
    }
    if (node.kind === "tool-group") {
      return (
        <ToolLineGroup
          key={nodeKey}
          tools={node.tools}
          isStreaming={live}
          turnKey={messageId}
          groupKey={nodeKey}
          conversationId={conversationId}
        />
      );
    }
    const step: ProcessStep = node.kind === "tool" ? node.step : node;
    const hasContentAfter =
      step.kind === "rework" &&
      nodes.slice(i + 1).some((n) => n.kind === "content");
    return (
      <ProcessRow
        key={nodeKey}
        step={step}
        streaming={live}
        reworkLabel={
          step.kind === "rework"
            ? reworkChipLabel(isStreaming, hasContentAfter)
            : undefined
        }
        citations={citations}
        citationToDisplay={citationToDisplay}
        knownLedgerIds={knownLedgerIds}
        evidenceLedger={evidenceLedger}
        turnKey={messageId}
        rowKey={nodeKey}
        conversationId={conversationId}
      />
    );
  };

  return (
    <div className="min-w-0 max-w-full space-y-2">
      {nodes.map((node, i) => {
        const prefix =
          i === fallbackBeforeTeamIdx
            ? renderFallback("fallback-before-team")
            : null;
        if (shouldCollapseProcess) {
          const isFirstProcess =
            isProcessNode(node) && !nodes.slice(0, i).some(isProcessNode);

          if (!processExpanded) {
            if (isProcessNode(node)) {
              if (!isFirstProcess) return null;
              return (
                <Fragment key={`sum-${nodeKeys[i]}`}>
                  {prefix}
                  <button
                    type="button"
                    onClick={toggleProcess}
                    className="inline-flex items-center gap-1 text-sm text-muted-foreground"
                  >
                    {processSummary}
                    <ChevronRight className="size-4 shrink-0" aria-hidden />
                  </button>
                </Fragment>
              );
            }
          } else if (isFirstProcess) {
            return (
              <Fragment key="process-expanded">
                {prefix}
                <button
                  type="button"
                  onClick={toggleProcess}
                  className="inline-flex items-center gap-1 text-sm text-muted-foreground"
                >
                  {processSummary}
                  <ChevronDown className="size-4 shrink-0" aria-hidden />
                </button>
                {renderNode(node, i)}
              </Fragment>
            );
          }
        }
        if (prefix) {
          return (
            <Fragment key={`wrap-${nodeKeys[i]}`}>
              {prefix}
              {renderNode(node, i)}
            </Fragment>
          );
        }
        return renderNode(node, i);
      })}
      {/* 无 team 标记的图兜底已移除（时间线一期）：多 Agent 回合必有 `team` 标记
          （live 盖章 + reload journal 补齐），图只在标记槽渲染。 */}
      {showFallbackAfter && renderFallback("fallback-after")}
      {isStreaming && composingTool && (
        <ComposingToolLine tool={composingTool} />
      )}
      {showThinkingTail && (
        <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          <ThinkingDots />
          Thinking…
        </span>
      )}
    </div>
  );
}

/** Re-export for consumers that imported from ProcessTimeline. */
export { ComposingToolLine } from "@/components/chat/ToolLine";
