import { Markdown } from "@/components/chat/Markdown";
import { ReceivedContextSection } from "@/components/chat/ReceivedContext";
import { CollapsibleSpeech } from "@/components/chat/debate/CollapsibleSpeech";
import { RunDetailList } from "@/components/chat/detail/RunDetailList";
import {
  absorbHandoffBriefContent,
  processHasSuccessfulHandoff,
} from "@/components/chat/handoffBrief";
import {
  ProcessEndChrome,
  TimelineNodeView,
} from "@/components/chat/message-bubble/ProcessTimeline";
import { RunInterveneControls } from "@/components/graph/RunInterveneControls";
import { runActCapabilities } from "@/components/graph/planCapabilities";
import { Badge, Button, IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useTurnAudit } from "@/hooks/useTurnAudit";
import { groupToolRuns, timelineNodeKeys } from "@/lib/processTimeline";
import type { AgentAuditEvent } from "@/services/audit";
import { permissionAxesShortLabel } from "@/services/permissionAxes";
import { activeRuntime, useConversationStore } from "@/stores/conversation";
import {
  continuationChains,
  hotfixSeatChain,
  useMessageRun,
} from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { turnDetailPath } from "@/stores/ui";
import { isLiveRunStatus } from "@agentcore/protocol-fold-kit";
import { MessagesSquare, Shield, Square } from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
  isDebateModeratorRun,
  isThinkingLivePlaceholder,
} from "./debateModerator";
import { receivedContextForList, selectRunTaskSection } from "./runTaskSection";
import { ContinuationChainSection } from "./sections/RunContinuationChain";
import { DebriefSection } from "./sections/RunDebrief";
import { EscalationSection } from "./sections/RunEscalations";
import { RunOutcomeAcceptSection } from "./sections/RunOutcomeAccept";
import { ResourceSection } from "./sections/RunResources";
import {
  SeatInstructionMark,
  SeatPriorSegments,
} from "./sections/SeatPriorSegments";
import { Section } from "./sections/shared";

/**
 * 本回合生效的权限配方短名（原安全台账「本回合模式 · X」快照）。回合审计里
 * ``permission.axes_snapshot`` 在回合入口写一次；扫最后一条 permission 快照/切换的
 * ``permission_axes`` 即当时生效档。兼容旧 ``preset_*`` 行。
 */
function turnPresetSnapshot(
  events: AgentAuditEvent[] | null | undefined,
): string | null {
  let label: string | null = null;
  for (const e of events ?? []) {
    if (
      e.action === "permission.axes_snapshot" ||
      e.action === "permission.axes_changed" ||
      e.action === "permission.preset_snapshot" ||
      e.action === "permission.preset_changed"
    ) {
      const raw =
        e.detail?.permission_axes ?? e.detail?.permission_preset ?? null;
      // 短名仅经 permissionAxesShortLabel（含 JSON 字符串解析）；禁把整段 JSON 当芯片标签。
      const short = permissionAxesShortLabel(raw);
      if (short) label = short;
    }
  }
  return label;
}

/**
 * Single-run detail content — hybrid layout aligned with the CEO bubble timeline:
 * header anchors (role / 接手 chip / 上下文 / 打开辩论室 / task / continuation /
 * escalation / context) → interleaved process rows → footer (debrief /
 * resources). Topology (depends / parent / children) lives on the
 * collab graph, not this inspector. The docked inspector is one virtual
 * list (chrome + each process row + foot) so the task scrolls away with
 * the log; CEO bubbles stay fully mapped.
 *
 * Bound to a specific message's execution slot (§9.3) via `messageId`, so the
 * conversation's right-side detail panel can pin a run from any turn (live or
 * historical) — the single home for run detail, reached from both the embedded
 * graph and the full-screen overlay. Chrome-free on purpose, so the drill-down
 * view is identical wherever it appears.
 */
export function RunDetailBody({
  messageId,
  runId,
  scrollParent = null,
}: {
  messageId: string;
  runId: string;
  /** Panel overflow from {@link RunDetailScroll}; null keeps an in-flow layout (tests). */
  scrollParent?: HTMLElement | null;
}) {
  const viewed = useMessageRun(messageId, runId);
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const navigate = useNavigate();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const turnInteractive = useConversationStore(
    (s) =>
      activeRuntime(s).messages.find((m) => m.id === messageId)?.isStreaming ??
      false,
  );

  const execution = viewed?.execution;
  const viewedRun = viewed?.run;
  const viewedAgent = viewed?.agent;
  const turnAudit = useTurnAudit(
    conversationId != null ? conversationId : null,
    messageId,
  );
  if (!execution || !viewedRun || !viewedAgent) return null;

  const seatChain = hotfixSeatChain(execution, viewedRun.id);
  const run = seatChain
    ? (seatChain.versions[seatChain.versions.length - 1]?.run ?? viewedRun)
    : viewedRun;
  const origin = seatChain?.versions[0]?.run ?? viewedRun;
  const agent =
    execution.agents.find((a) => a.id === run.agentId) ?? viewedAgent;
  const originAgent =
    execution.agents.find((a) => a.id === origin.agentId) ?? viewedAgent;
  const runCaps = runActCapabilities(execution, run.id);

  // 整轮停只给 captain：队员栏再夹一枚方块停止，会和「停止这位队员」看起来像同一件事。
  // 整轮硬停的主入口是输入框。「跑完再说」走输入框排队，不在右坞再放填草稿入口。
  const isCaptainRun = run.kind === "captain";
  const working = agent.status === "working";
  const stopTurnAction =
    working && isCaptainRun ? (
      <SimpleTooltip label="结束整轮（所有队员一起停）">
        <IconButton
          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
          aria-label="停止整轮"
          onClick={() => useConversationStore.getState().stopGeneration()}
        >
          <Square size={13} />
        </IconButton>
      </SimpleTooltip>
    ) : null;
  // 按人干预（只改这个人 / 只停这个人）：只在 `isLiveRunStatus` 时挂载（running /
  // pending）。终局整条不渲染、也不写灰字原因——死按钮没有教学价值。排队仍画：可停；
  // 改方向继续变灰 +「还没开工」。动作收进标题行纯图标。
  //
  // captain 除外（手机早有这道护栏）：主管这一路就是这条对话本身，「只停这位队员」对它
  // 无意义，引擎的计划里也没有它——出了按钮就是许一个必然落空的愿。要停就停整轮。
  const memberIntervene =
    conversationId != null && !isCaptainRun && isLiveRunStatus(run.status);
  const thinkingLive = isThinkingLivePlaceholder(agent);

  const isModerator = isDebateModeratorRun(execution, origin.id);
  const debateChain = seatChain
    ? null
    : (continuationChains(execution).find((c) =>
        c.versions.some((v) => v.run.id === viewedRun.id),
      ) ?? null);
  const taskSection = selectRunTaskSection(origin);
  const contextBlocks = receivedContextForList(
    origin.receivedContext,
    taskSection.promotedTask,
  );

  const rawTurnPreset = turnPresetSnapshot(turnAudit.data?.data);
  const turnPresetLabel = rawTurnPreset;

  const process = run.process;
  // 主持人 thinking 声明 false：working 且无 process 时不出空时间线；有 process 与其它 run 相同。
  const showTimeline =
    process.length > 0 ||
    thinkingLive ||
    (agent.toolProgress != null && agent.status === "working") ||
    (agent.status === "working" && !isModerator);

  const showDebrief =
    Boolean(run.debrief) && !processHasSuccessfulHandoff(process);
  const showConclusion = Boolean(run.outputSummary) && !run.debrief;
  const showResources = Boolean(
    (seatChain?.versions.some((v) => v.run.usage || v.run.cost) ?? false) ||
      run.usage ||
      run.cost,
  );
  const nodes = showTimeline
    ? groupToolRuns(absorbHandoffBriefContent(process, run.debrief))
    : [];
  const nodeKeys = timelineNodeKeys(nodes).map((k) => `${run.id}:${k}`);
  const timelineKey = `${messageId}:${run.id}`;
  const live = agent.status === "working";

  const headerStart = (
    <>
      <span className="flex-1 truncate text-sm font-medium text-foreground">
        {originAgent.role}
      </span>
      {run.replacesRunId != null && (
        <Badge
          tone="muted"
          pill
          title="同角色新人按新方向重做"
          className="font-medium"
        >
          接手
        </Badge>
      )}
      {turnPresetLabel && (
        <Badge
          tone="muted"
          pill
          title="本回合生效的权限模式"
          className="gap-1 font-medium"
        >
          <Shield size={11} className="shrink-0" />
          {turnPresetLabel}
        </Badge>
      )}
      {isModerator && conversationId != null && (
        <Button
          variant="ghost"
          className="h-auto shrink-0 px-0 py-0 text-xs text-primary hover:bg-transparent"
          icon={<MessagesSquare size={12} />}
          onClick={() => {
            navigate(turnDetailPath(conversationId, messageId, "debate"));
          }}
        >
          打开辩论室
        </Button>
      )}
      <ReceivedContextSection
        key={origin.id}
        blocks={contextBlocks}
        process={origin.process}
      />
    </>
  );

  const head = (
    <>
      {memberIntervene ? (
        <RunInterveneControls
          conversationId={conversationId}
          executionId={execution.id}
          runId={run.id}
          runStatus={run.status}
          role={originAgent.role}
          redirectCapable={runCaps.runRedirect}
          headerStart={headerStart}
        />
      ) : (
        <div className="mb-4 flex items-center gap-2">
          {headerStart}
          {stopTurnAction}
        </div>
      )}

      <Section title={taskSection.title}>
        <CollapsibleSpeech
          contentKey={taskSection.body}
          fadeToClass="from-card"
          sceneKey={`run:${origin.id}:task`}
        >
          <Markdown content={taskSection.body} />
        </CollapsibleSpeech>
      </Section>

      {debateChain && (
        <ContinuationChainSection
          chain={debateChain}
          currentRunId={viewedRun.id}
          agents={execution.agents}
          execution={execution}
          onSelect={(rid, role) => showRunDetail(messageId, rid, role)}
        />
      )}

      {seatChain ? (
        <SeatPriorSegments
          versions={seatChain.versions.slice(0, -1)}
          conversationId={conversationId}
          messageId={messageId}
        />
      ) : null}
      {seatChain ? <SeatInstructionMark run={run} /> : null}

      {(seatChain?.versions ?? [{ run }]).map(({ run: er }) =>
        er.escalations.length > 0 ? (
          <EscalationSection
            key={er.id}
            run={er}
            role={originAgent.role}
            conversationId={conversationId}
            interactive={turnInteractive}
          />
        ) : null,
      )}

      {run.error && (
        <Section title="错误">
          <p className="whitespace-pre-wrap break-words text-xs text-destructive">
            {run.error}
          </p>
        </Section>
      )}

      {/* 跑一半改方向 · 忽略路径收口 (Step 4): a terminal run whose「改方向」steer couldn't apply —
          surface it + let the user record an explicit accept.
          Gated to terminal runs so an in-flight run never triggers the audit read. */}
      {conversationId != null &&
        (seatChain?.versions ?? [{ run }]).map(({ run: er }) =>
          er.status !== "pending" && er.status !== "running" ? (
            <RunOutcomeAcceptSection
              key={er.id}
              conversationId={conversationId}
              messageId={messageId}
              runId={er.id}
            />
          ) : null,
        )}
    </>
  );

  const foot =
    showTimeline || showDebrief || showConclusion || showResources ? (
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
        {showDebrief && run.debrief ? (
          <DebriefSection debrief={run.debrief} />
        ) : showConclusion && run.outputSummary ? (
          <Section title="结论">
            <Markdown content={run.outputSummary} />
          </Section>
        ) : null}
        {showResources &&
          (seatChain?.versions ?? [{ run }]).map(({ run: er }) => {
            const erAgent =
              execution.agents.find((a) => a.id === er.agentId) ?? agent;
            if (!er.usage && !er.cost) return null;
            return (
              <ResourceSection
                key={er.id}
                run={er}
                agent={erAgent}
                keyBase={`run:${er.id}`}
              />
            );
          })}
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
      renderNode={(node, i) => (
        <TimelineNodeView
          node={node}
          nodeKey={nodeKeys[i] ?? String(i)}
          live={live && i === nodes.length - 1}
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
