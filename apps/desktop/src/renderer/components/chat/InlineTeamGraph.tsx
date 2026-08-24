import { DebateProgressLine } from "@/components/chat/DebateProgressLine";
import { GraphAppendAnchor } from "@/components/chat/GraphAppendAnchor";
import { StatusStrip } from "@/components/chat/StatusStrip";
import { TeamNotesPanel } from "@/components/chat/TeamNotesPanel";
import { teamGraphVisible } from "@/components/chat/debatePreviewPlacement";
import { teamNotesDefaultExpanded } from "@/components/chat/teamNotesDefaults";
import { GraphView } from "@/components/graph/GraphView";
import {
  journalHydrateIdentity,
  journalHydrateIdentityEqual,
} from "@/components/graph/journalHydrate";
import { executionGraphCapabilities } from "@/components/graph/planCapabilities";
import { ContextualTip } from "@/components/onboarding/ContextualTip";
import {
  EMBED_DEFAULT_COL_WIDTH,
  estimateBbox,
  fitWidthBox,
  workerGraphShape,
} from "@/lib/elk-layout";
import { ensureFullMessageRuns } from "@/services/messages";
import { useConversationStore } from "@/stores/conversation";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import {
  type ExecutionJournal,
  ExecutionScopeContext,
  isDebate,
  projectRuntime,
  useExecutionStore,
  useMessageExecution,
} from "@/stores/execution";
import { useGraphStore } from "@/stores/graph";
import { useSidePanelStore } from "@/stores/sidePanel";
import { turnDetailPath } from "@/stores/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

/** Re-export gate used by fixture tests and graph consumers. */
export {
  shouldShowTeamGraph,
  teamHasStartedRuns,
} from "@/components/chat/debatePreviewPlacement";

/**
 * The multi-agent turn's primary surface, embedded in the assistant message
 * at the `team` marker slot (below the CEO lead-in content step): a compact
 * status strip (lifecycle + cost) over the live collaboration graph.
 *
 * Per-message (§9.3): keyed by the assistant message id, so live and reloaded
 * (historical) multi-agent turns render identically — the live turn streams into
 * the slot, a reloaded turn hydrates it from the persisted journal (`runs`), and
 * both project through the same fold. Node clicks drill into the passive
 * right-side panel; 「在画布打开」/「回放」navigate to the turn's full-screen
 * detail page (`#/conversations/:id/turn/:turnId`).
 */
export function InlineTeamGraph({
  messageId,
  executionId,
  journal,
}: {
  messageId: string;
  executionId: string;
  journal?: ExecutionJournal;
}) {
  const navigate = useNavigate();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const hydrateFromJournal = useExecutionStore((s) => s.hydrateFromJournal);
  const journalIdentity = journalHydrateIdentity(journal);
  const journalIdentityRef = useRef(journalIdentity);
  if (
    !journalHydrateIdentityEqual(journalIdentityRef.current, journalIdentity)
  ) {
    journalIdentityRef.current = journalIdentity;
  }
  const stableJournalIdentity = journalIdentityRef.current;
  useEffect(() => {
    if (journal?.eventsComplete === false) {
      if (conversationId) {
        void ensureFullMessageRuns(conversationId, messageId);
      }
      return;
    }
    if (stableJournalIdentity) {
      hydrateFromJournal(messageId, stableJournalIdentity.journal);
    }
  }, [
    stableJournalIdentity,
    messageId,
    hydrateFromJournal,
    conversationId,
    journal?.eventsComplete,
  ]);

  const execution = useMessageExecution(messageId);
  // 「打开辩论室」/「在画布打开」/「回放」→ 全屏回合详情；辩论回合传 view=debate
  //（与右坞 RunDetailBody / RunModeratorLedger 深链一致）。
  const openInCanvas = useCallback(
    (autoplay: boolean) => {
      if (!conversationId) return;
      const view =
        execution && isDebate(execution) ? ("debate" as const) : undefined;
      navigate(
        turnDetailPath(conversationId, messageId, view, undefined, {
          autoplay,
        }),
      );
    },
    [conversationId, messageId, navigate, execution],
  );
  const [measured, setMeasured] = useState<{
    height: number;
    overflowing: boolean;
  } | null>(null);
  const onMeasure = useCallback(
    (m: { height: number; overflowing: boolean }) => setMeasured(m),
    [],
  );
  const layoutKind = useGraphStore((s) => s.layoutKind);
  const fallbackHeight = useMemo(() => {
    if (!execution) return 0;
    const est = estimateBbox(workerGraphShape(execution.runs), layoutKind);
    return fitWidthBox(est.width, est.height, EMBED_DEFAULT_COL_WIDTH).height;
  }, [execution, layoutKind]);

  const caps = executionGraphCapabilities(execution);
  // 进行中（含跨回合追加重开）自动展开；收场后尊重用户折叠偏好。
  const graphLive =
    execution?.status === "running" || execution?.status === "paused";
  const [expanded, , setExpanded] = useStreamAwareDisclosure(
    `${messageId}:inline-graph`,
    Boolean(graphLive),
    { liveDefault: true, settledDefault: caps.inlineDefaultExpanded },
  );

  // 便签墙：运行中默认展开、结束默认折叠；用户选择跨卸载/刷新保留。
  const notesLive = teamNotesDefaultExpanded(
    execution?.status,
    execution?.teamNotes ?? [],
  );
  const [notesExpanded, , setNotesExpanded] = useStreamAwareDisclosure(
    `${messageId}:team-notes`,
    notesLive,
  );

  if (
    !execution ||
    execution.id !== executionId ||
    !caps.showsTeamGraph ||
    !teamGraphVisible(execution.runs)
  ) {
    return null;
  }

  const graphHeight = measured?.height ?? fallbackHeight;
  // 推进线（认知轨迹）位置随辩论状态分治：进行中留在标题下方，让用户不点开也能瞥当前轮焦点；
  // 收场后标题已给出结论，推进线降权下移到协作图之后，头部只留一条结论行（前端UX设计.md §三）。
  const debateLive =
    isDebate(execution) &&
    (execution.status === "running" || execution.status === "paused");
  const debateSettled = isDebate(execution) && !debateLive;

  return (
    <ExecutionScopeContext.Provider value={messageId}>
      <ContextualTip tipId="inline_team_graph" placement="top" active>
        <div className="animate-task-card-enter mb-3 overflow-hidden rounded-xl border border-border bg-card">
          {/* 辩论全过程 / 版本对比等「过程产物」不再内联聊天——它们归全屏放大态（统一辩论室 /
              统一「对比」视图），聊天正文状态条只留战绩 + 入口 CTA
              （协作图与双视图UX.md §六 两个入口：聊天内嵌 ⇄ 全屏放大）。 */}
          {execution.prevExecutionId ? (
            <div className="border-b border-border/60 px-3 py-2">
              <GraphAppendAnchor
                prevExecutionId={execution.prevExecutionId}
                actKind={execution.acts[0]?.kind}
                authorizedBy={execution.acts[0]?.authorizedBy}
              />
            </div>
          ) : null}
          <StatusStrip
            execution={execution}
            expanded={expanded}
            onToggle={() => setExpanded(!expanded)}
            onMaximize={() => openInCanvas(false)}
            onReplay={() => openInCanvas(true)}
          />
          {debateLive && (
            <DebateProgressLine
              execution={execution}
              disclosureKey={`${messageId}:debate-progress`}
            />
          )}
          {/* 折叠优先隐藏 GraphArea，勿卸载 ReactFlow（白板宿主常驻）。 */}
          <div
            className={expanded ? undefined : "hidden"}
            aria-hidden={!expanded}
          >
            <GraphArea
              messageId={messageId}
              height={graphHeight}
              onMeasure={onMeasure}
            />
          </div>
          {debateSettled && (
            <DebateProgressLine
              execution={execution}
              disclosureKey={`${messageId}:debate-progress`}
            />
          )}
          {/* 团队便签墙 (§2.2 通): collapsible; stays available when the graph is folded.
              Empty turns render nothing. */}
          <TeamNotesPanel
            notes={execution.teamNotes}
            expanded={notesExpanded}
            onExpandedChange={setNotesExpanded}
          />
        </div>
      </ContextualTip>
    </ExecutionScopeContext.Provider>
  );
}

function GraphArea({
  messageId,
  height,
  onMeasure,
}: {
  messageId: string;
  height: number;
  onMeasure: (m: { height: number; overflowing: boolean }) => void;
}) {
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  // Stable callback so memo(GraphView) skips while StatusStrip eats live execution.
  const onNodeSelect = useCallback(
    (runId: string) => {
      const rt = useExecutionStore.getState().byId[messageId];
      const execution = rt ? projectRuntime(rt) : null;
      const run = execution?.runs.find((r) => r.id === runId);
      const role = execution?.agents.find((a) => a.id === run?.agentId)?.role;
      showRunDetail(messageId, runId, role);
    },
    [messageId, showRunDetail],
  );

  return (
    <div
      className="w-full select-none border-t border-border"
      style={{ height }}
    >
      <GraphView
        interactive={false}
        fitMode="width"
        onNodeSelect={onNodeSelect}
        onMeasure={onMeasure}
      />
    </div>
  );
}
