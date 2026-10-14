import { InlineTeamGraph } from "@/components/chat/InlineTeamGraph";
import { InterjectionTimeline } from "@/components/chat/InterjectionTimeline";
import { Markdown } from "@/components/chat/Markdown";
import {
  ComposingToolLine,
  ToolLine,
  ToolLineGroup,
} from "@/components/chat/ToolLine";
import { shouldShowTeamGraph } from "@/components/chat/debatePreviewPlacement";
import { absorbHandoffBriefContent } from "@/components/chat/handoffBrief";
import { executionGraphCapabilities } from "@/components/graph/planCapabilities";
import {
  type ProcessTurnLane,
  type TimelineNode,
  groupToolRuns,
  processFoldMask,
  processTurnLane,
  timelineNodeKeys,
} from "@/lib/processTimeline";
import type { CheckpointDisplay } from "@/stores/conversation";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import {
  type ExecutionJournal,
  isDebate,
  useMessageExecution,
} from "@/stores/execution";
import { renderTimelineInteractionCard } from "@/stores/interactions/registryUi";
import type {
  Citation,
  ProcessStep,
  RunDebrief,
  TurnEvidenceLedgerEntry,
} from "@/types/events";
import { type ReactNode, memo } from "react";
import { LiveWaitLabel } from "./LiveFlow";
import { ThinkingHeader } from "./Thinking";

/** Thought 折叠覆盖面：推理/工具 + 弱式决策痕迹（批准/委派授权/推进卡）
 * + 已答复 ask / 已结算开工复核。用户可见正文（含中间段）、待拍板、协作图、插话仍外置可见。
 * 收起摘要是答案 caption（Cursor / ChatGPT），不与答案平级占时间线行距。 */

type ProcessTurnFace = "summary" | ProcessTurnLane;

function processTurnFaceClass(face: ProcessTurnFace): string {
  switch (face) {
    case "summary":
      return "process-summary";
    case "answer":
      return "process-answer";
    case "slot":
      return "process-slot";
    default:
      return "process-chrome";
  }
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

const InlineReasoning = memo(function InlineReasoning({
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
        <div className="mt-2 min-w-0 max-w-full text-muted-foreground">
          <Markdown content={text} isStreaming={streaming} muted />
        </div>
      )}
    </div>
  );
});

const ProcessRow = memo(function ProcessRow({
  step,
  streaming,
  citations,
  knownLedgerIds,
  evidenceLedger,
  turnKey,
  rowKey,
  conversationId,
}: {
  step: ProcessStep;
  streaming: boolean;
  citations: Citation[];
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
          conversationId={conversationId}
          citations={citations}
          knownLedgerIds={knownLedgerIds}
          evidenceLedger={evidenceLedger}
          isStreaming={streaming}
        />
      </div>
    );
  }
  // Positional markers (team/checkpoint) are resolved in the timeline
  // map, never routed here — only a `tool` step reaches this tail.
  if (step.kind === "tool")
    return (
      <ToolLine step={step} turnKey={turnKey} conversationId={conversationId} />
    );
  return null;
});

export function TimelineNodeView({
  node,
  nodeKey,
  live,
  citations,
  knownLedgerIds = null,
  evidenceLedger = null,
  messageId,
  journal,
  conversationId,
  checkpoints,
  isStreaming,
}: {
  node: TimelineNode;
  nodeKey: string;
  live: boolean;
  citations: Citation[];
  knownLedgerIds?: ReadonlySet<string> | null;
  evidenceLedger?: readonly TurnEvidenceLedgerEntry[] | null;
  messageId?: string;
  journal?: ExecutionJournal;
  conversationId: string | null;
  checkpoints: CheckpointDisplay[];
  isStreaming: boolean;
}) {
  if (node.kind === "team") {
    return messageId ? (
      <InlineTeamGraph
        messageId={messageId}
        executionId={node.execution_id}
        journal={journal}
      />
    ) : null;
  }
  if (node.kind === "user_interjection") {
    return messageId ? (
      <InterjectionTimeline
        messageId={messageId}
        interjectionId={node.interjection_id}
      />
    ) : null;
  }
  if (
    node.kind === "checkpoint" ||
    node.kind === "escalation" ||
    node.kind === "approval"
  ) {
    const card = renderTimelineInteractionCard(
      node.kind,
      node,
      {
        checkpoints,
      },
      {
        messageId: messageId ?? "",
        conversationId,
        interactive: isStreaming,
      },
    );
    if (!card) return null;
    return <div className="[&>:first-child]:mt-0">{card}</div>;
  }
  if (node.kind === "tool-group") {
    return (
      <ToolLineGroup
        tools={node.tools}
        isStreaming={live}
        turnKey={messageId}
        groupKey={nodeKey}
        conversationId={conversationId}
      />
    );
  }
  const step: ProcessStep = node.kind === "tool" ? node.step : node;
  return (
    <ProcessRow
      step={step}
      streaming={live}
      citations={citations}
      knownLedgerIds={knownLedgerIds}
      evidenceLedger={evidenceLedger}
      turnKey={messageId}
      rowKey={nodeKey}
      conversationId={conversationId}
    />
  );
}

/** Marker that anchors the collaboration graph slot. */
export function graphSlotExecutionId(
  step: ProcessStep | undefined,
): string | null {
  if (step?.kind === "team") return step.execution_id;
  return null;
}

/**
 * In-stream fallback: generic Thinking… when the tail has no live node.
 * Live chrome = running tool, streaming reasoning/content,
 * composing tool, visible collaboration graph (StatusStrip) at the tail, or a
 * pending user gate. Markers are not live by themselves — delegate/debate omit
 * tool steps (isMarkerStandinTool) and stand in as `team` / interaction markers.
 */
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
  if (last.kind === "tool") {
    return last.status !== "running";
  }
  return true;
}

/** Composing-tool + Thinking… tail — bubble and run-detail foot share this. */
export function ProcessEndChrome({
  process,
  isStreaming,
  composingTool,
  messageId,
  checkpoints,
}: {
  process: ProcessStep[];
  isStreaming: boolean;
  composingTool: { toolName: string; chars: number } | null;
  messageId?: string;
  checkpoints: CheckpointDisplay[];
}) {
  const execution = useMessageExecution(messageId ?? null);
  const last = process[process.length - 1];
  const graphVisibleAtTail = (() => {
    if (!execution || !isDebate(execution)) return false;
    const slotExecutionId = graphSlotExecutionId(last);
    if (!slotExecutionId) return false;
    if (!execution || execution.id !== slotExecutionId) return false;
    if (!executionGraphCapabilities(execution).showsTeamGraph) return false;
    return shouldShowTeamGraph(execution.runs);
  })();
  const pendingUserGate = checkpoints.some((c) => c.status === "pending");
  const showThinkingTail = shouldShowThinkingTail({
    isStreaming,
    composingTool: Boolean(composingTool),
    last,
    graphVisibleAtTail,
    pendingUserGate,
  });
  return (
    <>
      {isStreaming && composingTool && (
        <ComposingToolLine tool={composingTool} />
      )}
      {showThinkingTail && <LiveWaitLabel>Thinking…</LiveWaitLabel>}
    </>
  );
}

export function ProcessTimeline({
  process,
  isStreaming,
  citations,
  knownLedgerIds = null,
  evidenceLedger = null,
  composingTool,
  fallbackContent,
  messageId,
  journal,
  conversationId,
  checkpoints,
  /** When false, never collapse reasoning/tool rows into a summary.
   * Default true keeps CEO bubble chrome. */
  collapseProcessSteps = true,
  handoffDebrief = null,
}: {
  process: ProcessStep[];
  isStreaming: boolean;
  citations: Citation[];
  knownLedgerIds?: ReadonlySet<string> | null;
  evidenceLedger?: readonly TurnEvidenceLedgerEntry[] | null;
  composingTool: { toolName: string; chars: number } | null;
  fallbackContent: string;
  messageId?: string;
  journal?: ExecutionJournal;
  conversationId: string | null;
  checkpoints: CheckpointDisplay[];
  collapseProcessSteps?: boolean;
  /** Harvested `run.debrief` — fills an empty successful handoff row. */
  handoffDebrief?: RunDebrief | null;
}) {
  const execution = useMessageExecution(messageId ?? null);
  const showInlineGraph = execution != null && isDebate(execution);
  const hasContentStep = process.some((s) => s.kind === "content");

  // 摘要步数与可见行同源，避免「Thought 10」展开只剩 3 行。
  const nodes = groupToolRuns(
    absorbHandoffBriefContent(process, handoffDebrief),
  );
  // 稳定 key（时间线一期）：insertBeforeTeam 中段插入不再位移后续行的 React key。
  const nodeKeys = timelineNodeKeys(nodes);

  const { reasoningCount, toolCount } = countProcessStats(nodes);
  const pendingGateIds = new Set(
    checkpoints.filter((c) => c.status === "pending").map((c) => c.id),
  );
  const foldMask = processFoldMask(nodes, pendingGateIds);
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
  // step), slot it before the first team marker — never after the
  // whole timeline (that put the graph above the CEO lead-in).
  const fallbackBeforeTeamIdx =
    !hasContentStep && fallbackContent
      ? nodes.findIndex((n) => n.kind === "team")
      : -1;
  const showFallbackAfter =
    !hasContentStep && Boolean(fallbackContent) && fallbackBeforeTeamIdx < 0;

  const answerMarkdown = (
    <div className="process-narration min-w-0 max-w-full text-foreground">
      <Markdown
        content={fallbackContent}
        conversationId={conversationId}
        citations={citations}
        knownLedgerIds={knownLedgerIds}
        evidenceLedger={evidenceLedger}
        isStreaming={isStreaming}
      />
    </div>
  );

  const renderNode = (node: TimelineNode, i: number) => (
    <TimelineNodeView
      node={node}
      nodeKey={nodeKeys[i] ?? String(i)}
      live={isStreaming && i === nodes.length - 1}
      citations={citations}
      knownLedgerIds={knownLedgerIds}
      evidenceLedger={evidenceLedger}
      messageId={messageId}
      journal={journal}
      conversationId={conversationId}
      checkpoints={checkpoints}
      isStreaming={isStreaming}
    />
  );

  type TurnItem = { key: string; face: ProcessTurnFace; node: ReactNode };
  const items: TurnItem[] = [];
  let summaryInserted = false;
  const pushSummary = (key: string) => {
    if (summaryInserted) return;
    summaryInserted = true;
    items.push({
      key,
      face: "summary",
      node: (
        <ThinkingHeader
          isStreaming={false}
          expanded={processExpanded}
          streamingLabel="Thinking…"
          doneLabel={processSummary}
          onToggle={toggleProcess}
        />
      ),
    });
  };

  for (let i = 0; i < nodes.length; i++) {
    if (i === fallbackBeforeTeamIdx) {
      items.push({
        key: "fallback-before-team",
        face: "answer",
        node: answerMarkdown,
      });
    }
    if (nodes[i]?.kind === "team" && !showInlineGraph) continue;
    if (shouldCollapseProcess && foldMask[i]) {
      if (!processExpanded) {
        pushSummary(`sum-${nodeKeys[i] ?? String(i)}`);
        continue;
      }
      pushSummary("process-expanded");
    }
    items.push({
      key: nodeKeys[i] ?? String(i),
      face: processTurnLane(nodes[i], foldMask[i] === true),
      node: renderNode(nodes[i], i),
    });
  }
  if (showFallbackAfter) {
    items.push({
      key: "fallback-after",
      face: "answer",
      node: answerMarkdown,
    });
  }

  return (
    <div className="process-turn">
      {items.map((item) => (
        <div key={item.key} className={processTurnFaceClass(item.face)}>
          {item.node}
        </div>
      ))}
      {/* 无 team 标记的图兜底已移除（时间线一期）：多 Agent 回合必有 `team` 标记
          （live 盖章 + reload journal 补齐），图只在标记槽渲染。 */}
      <ProcessEndChrome
        process={process}
        isStreaming={isStreaming}
        composingTool={composingTool}
        messageId={messageId}
        checkpoints={checkpoints}
      />
    </div>
  );
}

/** Re-export for consumers that imported from ProcessTimeline. */
export { ComposingToolLine } from "@/components/chat/ToolLine";
