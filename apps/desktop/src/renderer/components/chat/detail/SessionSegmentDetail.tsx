/**
 * 会话图上一段的读：标题是这件事的全文，下面按时间读它怎么做完的。
 * 辩论详情不走这里。
 */

import { Markdown } from "@/components/chat/Markdown";
import { RunDetailList } from "@/components/chat/detail/RunDetailList";
import {
  ProcessEndChrome,
  TimelineNodeView,
} from "@/components/chat/message-bubble/ProcessTimeline";
import { failureDetailSentence } from "@/components/graph/agentNode/shared";
import { RewindFromHere } from "@/components/graph/RewindFromHere";
import { RunInterveneControls } from "@/components/graph/RunInterveneControls";
import { pickCostMoney } from "@/lib/format";
import { groupToolRuns, timelineNodeKeys } from "@/lib/processTimeline";
import type {
  AgentState,
  ContinuationChain,
  Execution,
  RunNode,
} from "@/stores/execution";
import type { ContextBlockWire } from "@/types/events";
import { EscalationSection } from "./sections/RunEscalations";
import { RunOutcomeAcceptSection } from "./sections/RunOutcomeAccept";
import { ResourceSection } from "./sections/RunResources";
import {
  SeatInstructionMark,
  SeatPriorSegments,
} from "./sections/SeatPriorSegments";

function upstreamConclusions(blocks: ContextBlockWire[]): string[] {
  return blocks
    .filter((block) => block.channel === "dependency" && block.body.trim())
    .map((block) => block.body.trim());
}

function hasMeter(run: RunNode): boolean {
  const tokens = run.usage ? run.usage.input + run.usage.output : 0;
  if (tokens > 0) return true;
  const money = pickCostMoney(run.cost);
  return money != null && money.nano > 0;
}

export function SessionSegmentDetail({
  messageId,
  scrollParent = null,
  conversationId,
  execution,
  run,
  origin,
  agent,
  originAgent,
  memberIntervene,
  turnInteractive,
  redirectCapable,
  seatChain,
}: {
  messageId: string;
  scrollParent?: HTMLElement | null;
  conversationId: string | null;
  execution: Execution;
  run: RunNode;
  origin: RunNode;
  agent: AgentState;
  originAgent: AgentState;
  memberIntervene: boolean;
  turnInteractive: boolean;
  redirectCapable: boolean;
  seatChain: ContinuationChain | null;
}) {
  const goal = (origin.task || originAgent.role || "这段").trim() || "这段";
  const conclusion = (run.outputSummary ?? "").trim();
  const showConclusion = conclusion.length > 0 && conclusion !== goal;
  const upstream = upstreamConclusions(origin.receivedContext);
  const process = run.process;
  const live = agent.status === "working";
  const showTimeline =
    process.length > 0 ||
    (agent.toolProgress != null && agent.status === "working") ||
    agent.status === "working";
  const nodes = showTimeline ? groupToolRuns(process) : [];
  const nodeKeys = timelineNodeKeys(nodes).map((key) => `${run.id}:${key}`);
  const timelineKey = `${messageId}:${run.id}`;
  const sealed =
    conversationId != null &&
    !turnInteractive &&
    run.status === "completed" &&
    run.kind !== "captain";

  const title = (
    <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
      {goal}
    </span>
  );

  const head = (
    <>
      {memberIntervene && conversationId != null ? (
        <RunInterveneControls
          conversationId={conversationId}
          executionId={execution.id}
          runId={run.id}
          runStatus={run.status}
          role={goal}
          redirectCapable={redirectCapable}
          headerStart={title}
        />
      ) : (
        <div className="mb-4 flex items-start gap-2">{title}</div>
      )}

      {sealed && conversationId != null && (
        <RewindFromHere
          conversationId={conversationId}
          messageId={messageId}
          runId={run.id}
        />
      )}

      {seatChain ? <SeatInstructionMark run={run} /> : null}

      {showConclusion && (
        <div className="mb-4 text-sm text-foreground">
          <Markdown content={conclusion} />
        </div>
      )}

      {seatChain ? (
        <SeatPriorSegments
          versions={seatChain.versions.slice(0, -1)}
          conversationId={conversationId}
          messageId={messageId}
        />
      ) : null}

      {upstream.length > 0 && (
        <div className="mb-4 space-y-2">
          <p className="text-xs font-medium text-muted-foreground">上一段</p>
          {upstream.map((body) => (
            <div key={body} className="text-sm text-foreground">
              <Markdown content={body} />
            </div>
          ))}
        </div>
      )}

      {run.status === "failed" && (
        <p className="mb-4 text-sm text-destructive">
          {failureDetailSentence(run.failureKind, run.productLanded)}
        </p>
      )}

      {run.escalations.length > 0 && (
        <EscalationSection
          run={run}
          role={originAgent.role}
          conversationId={conversationId}
          interactive={turnInteractive}
        />
      )}

      {conversationId != null &&
        run.status !== "pending" &&
        run.status !== "running" && (
          <RunOutcomeAcceptSection
            conversationId={conversationId}
            messageId={messageId}
            runId={run.id}
          />
        )}
    </>
  );

  const metered = hasMeter(run);
  const foot =
    showTimeline || metered ? (
      <>
        {showTimeline && (
          <ProcessEndChrome
            process={process}
            isStreaming={live}
            composingTool={live ? agent.toolProgress : null}
            messageId={timelineKey}
            checkpoints={[]}
          />
        )}
        {metered && (
          <ResourceSection run={run} agent={agent} keyBase={`run:${run.id}`} />
        )}
      </>
    ) : null;

  return (
    <RunDetailList
      scrollParent={scrollParent}
      head={head}
      foot={foot}
      nodes={nodes}
      nodeKeys={nodeKeys}
      isStreaming={live}
      renderNode={(node, index) => (
        <TimelineNodeView
          node={node}
          nodeKey={nodeKeys[index] ?? String(index)}
          live={live && index === nodes.length - 1}
          citations={[]}
          messageId={timelineKey}
          conversationId={conversationId}
          checkpoints={[]}
          isStreaming={live}
        />
      )}
    />
  );
}
