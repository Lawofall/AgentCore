import {
  AssistantMessageFooter,
  FinishReasonChip,
} from "@/components/AssistantMessageFooter";
import { DebateView, LiveDebateNarrative } from "@/components/DebateView";
import {
  InterjectionBubbles,
  type InterjectionItem,
} from "@/components/InterjectionBubbles";
import { Markdown } from "@/components/Markdown";
import {
  ProcessTimeline,
  Reasoning,
  type TeamProjection,
  graphAppendAnchorLabel,
  kickoffReleasedFromCold,
  prevGraphAnchorLabel,
  shouldShowTeamGraph,
} from "@/components/ProcessTimeline";
import { SourceCards, buildCitationDisplayMap } from "@/components/SourceCards";
import { TeamView } from "@/components/TeamView";
import { UnproductiveToolFailureHint } from "@/components/UnproductiveToolFailureHint";
import { useColdInteractions } from "@/lib/coldInteractions";
import type { SupportDiagnosticIds } from "@/lib/supportDiagnostics";
import type {
  EscalationSlot,
  HotDecisionTrace,
  StageCardTrace,
} from "@/protocol/fold";
import type { TeamPreviewTrace } from "@/protocol/teamPreviewTraces";
// Rich assistant rendering shared by live turns and history replay (前端技术与架构 §七 ·
// 富渲染 + 多 Agent 团队视图). One {@link AssistantContent} consumes the same fields whether
// they come from the live fold (ProjectedTurn) or a persisted message (MessageDetail).
//
// 统一团队时间线: every turn renders its `process` timeline (正文 / 思考 / 工具, interleaved);
// for history it is restored from MessageDetail.runs.process. A multi-agent turn additionally
// carries a `team` positional marker in that timeline — the collaboration graph slots inline
// at the marker (协作图时间线落点), re-folded from MessageDetail.runs.events for history.
// The checkpoint·ask·plan_review markers are anchors for desktop's inline cards; mobile owns
// those interactions via PauseCard / ResumeCard. team_preview pending 仍 no-op；resolved
// 痕迹（已调整 · 已交回修订）走时间线。
// user_interjection markers pin mid-turn 插话 bubbles at their chronological slot (正文/五态
// 旁路查 userInterjections)；旧 journal 无 marker 时由调用方挂尾部回退条。
//
// Citations render as a source list under the message either way.
import type {
  Citation,
  ContextBlockWire,
  DebateNarrativeRound,
  DebateResultPayload,
  EvidenceLedgerEntry,
  ProcessStep,
  ToolPhase,
  UsageBreakdown,
} from "@agentcore/contract-types";
import { useMemo, useRef } from "react";

export {
  graphAppendAnchorLabel,
  kickoffReleasedFromCold,
  prevGraphAnchorLabel,
  shouldShowTeamGraph,
  type TeamProjection,
};
export {
  AssistantMessageFooter,
  FinishReasonChip,
} from "@/components/AssistantMessageFooter";

export function AssistantContent({
  process,
  content,
  reasoning,
  citations,
  citationToDisplay: citationToDisplayProp,
  evidenceLedger,
  isStreaming = false,
  messageId,
  captainContext,
  team,
  debate,
  debateRounds,
  escalationSlots,
  hotTraces,
  stageCardTraces,
  teamPreviewTraces,
  toolPhases,
  graphAppendActKinds,
  graphAppendAuthorizedBy,
  prevExecutionIds,
  userInterjections,
  turnClosed = false,
  onFill,
  supportIds,
  onOpenBrowserLive,
  finishReason,
  finishDiagnosisLabel,
  failureNotice,
  usage,
  rounds,
  costText,
  durationMs,
  clockIso,
}: {
  process?: ProcessStep[];
  content: string;
  reasoning?: string;
  citations?: Citation[];
  /** Canonical→display citation map（可选；未传时本组件算一次下发 Markdown / SourceCards）。 */
  citationToDisplay?: ReadonlyMap<number, number>;
  /** 回合调研台账（`#rN`）：live=`ProjectedTurn.evidenceLedger`；history=`MessageDetail.evidenceLedger`。
   *  与 `team.evidenceLedger`（辩论场级 `#eN`）是两通道，勿混用。 */
  evidenceLedger?: EvidenceLedgerEntry[];
  /** Live turn → ProcessTimeline Thought/tool stream-aware disclosure + Markdown streaming. */
  isStreaming?: boolean;
  /** Turn message id（可选；时间线披露稳定键）。 */
  messageId?: string | null;
  /** 收到的上下文 · CEO 侧 (上下文传递可视化 通道①): what the CEO captain actually read this
   *  turn (系统提示 / 对话历史 / 原始请求). Entry lives in AssistantMessageFooter「更多」
   *  (aligned with desktop); present even on a pure-chat turn (no team). */
  captainContext?: ContextBlockWire[];
  team?: TeamProjection;
  debate?: DebateResultPayload | null;
  /** 辩论进行中的逐轮叙事 (fold 的 `debateRounds`)：`debate` 收场产物未到时实时叠出主持人逐
   *  轮焦点 / 小结 / 裁判；收场后让位给 {@link DebateView} 的全量双产物。 */
  debateRounds?: DebateNarrativeRound[];
  /** 升级时间线槽 (统一时间线二期): escalation_id → card body (extractEscalationSlots). */
  escalationSlots?: Map<string, EscalationSlot>;
  /** 热审批/委派授权痕迹 (D3): id → resolved 轻行内容 (extractHotDecisionTraces). */
  hotTraces?: Map<string, HotDecisionTrace>;
  /** 阶段推进卡时间线痕迹：id → resolved/orphaned 轻行 (extractStageCardTraces)。 */
  stageCardTraces?: Map<string, StageCardTrace>;
  /** 开工卡 / 开赛卡 resolved 痕迹（extractTeamPreviewTraces）。 */
  teamPreviewTraces?: Map<string, TeamPreviewTrace>;
  /** 工具执行阶段进度 (联网搜索前端展示优化): tool_call_id → latest coarse phase for a still-running
   *  tool, from the transport-only live sibling {@link extractToolPhases}. Live turns only; absent
   *  on history replay (the events are never journaled) → tool rows show plain status. */
  toolPhases?: Map<string, ToolPhase>;
  /** graph_append 开幕 kind（旧 journal；`extractGraphAppendActKinds`）。 */
  graphAppendActKinds?: Map<string, string>;
  /** graph_append 授权来源（旧 journal；`extractGraphAppendAuthorizedBy`）。 */
  graphAppendAuthorizedBy?: Map<string, string>;
  /** execution_id → prev_execution_id（新契约「续自」；`extractPrevExecutionIds`）。 */
  prevExecutionIds?: Map<string, string>;
  /** 旁路插话叶；process 上 `user_interjection` marker 按 id 查。无 process / 无 marker
   *  的旧 journal 由组件尾部回退条渲染。 */
  userInterjections?: readonly InterjectionItem[];
  /** 回合已收口 → received 派生态「未被主 Agent 读取」。 */
  turnClosed?: boolean;
  /** Tap an ask/chip → fill the composer (回填输入框, review before send). Absent → chips
   *  render but no-op (e.g. a read-only context with no composer). */
  onFill?: (text: string) => void;
  /** 复制排查包 ids（报障 / Cursor 日志查询）；有任一 id 即在复制行显示入口. */
  supportIds?: SupportDiagnosticIds;
  /** Open BrowserLiveSheet from browser_login EscalationAnswer. */
  onOpenBrowserLive?: (opts?: { runId?: string }) => void;
  /** Abnormal finish chip (max_rounds / degraded / …); streaming hides. */
  finishReason?: string | null;
  /** Degraded chip diagnosis suffix. */
  finishDiagnosisLabel?: string;
  /** Empty-failure visible notice (structured error / emptyFailureNotice) for copy/export. */
  failureNotice?: string | null;
  usage?: UsageBreakdown | null;
  rounds?: number | null;
  costText?: string | null;
  durationMs?: number | null;
  clockIso?: string | null;
}) {
  const coldById = useColdInteractions();
  const kickoffReleased = kickoffReleasedFromCold(coldById.values(), messageId);
  const hasTeam = !!team && team.runs.length > 0;
  // 开工挂起不出图；授权后 pending 编制也出图。
  const showTeamGraph =
    !!team && shouldShowTeamGraph(team.runs, kickoffReleased);
  const turnLedger = evidenceLedger;

  // Display renumbering: append-only across stream frames so assigned numbers never jump.
  // Markdown chips 与 SourceCards 共用同一 displayMap（本组件算一次下发）。
  const prevDisplayRef = useRef<Map<number, number>>(new Map());
  const prevMessageIdRef = useRef(messageId);
  if (prevMessageIdRef.current !== messageId) {
    prevMessageIdRef.current = messageId;
    prevDisplayRef.current = new Map();
  }
  const citationDisplay = useMemo(() => {
    const list = citations ?? [];
    if (list.length === 0) return null;
    const next = buildCitationDisplayMap(
      content,
      list.length,
      prevDisplayRef.current,
      list,
    );
    prevDisplayRef.current = next.stableCited;
    return next;
  }, [content, citations]);
  const citationToDisplay = citationToDisplayProp ?? citationDisplay?.toDisplay;

  const markedInterjectionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const s of process ?? []) {
      if (s.kind === "user_interjection" && s.interjection_id) {
        ids.add(s.interjection_id);
      }
    }
    return ids;
  }, [process]);
  const unmarkedInterjections = useMemo(
    () =>
      (userInterjections ?? []).filter(
        (u) => !markedInterjectionIds.has(u.interjectionId),
      ),
    [userInterjections, markedInterjectionIds],
  );

  return (
    <>
      {!isStreaming && !failureNotice && (
        <FinishReasonChip
          reason={finishReason}
          diagnosisLabel={finishDiagnosisLabel}
        />
      )}
      {debate ? (
        <DebateView debate={debate} onFill={onFill} />
      ) : debateRounds && debateRounds.length > 0 ? (
        <LiveDebateNarrative rounds={debateRounds} />
      ) : null}
      {process && process.length > 0 ? (
        // 统一团队时间线: the team graph rides its inline `team` marker; escalation /
        // approval / delegation markers render at their own slots (二期).
        <ProcessTimeline
          steps={process}
          citations={citations}
          citationToDisplay={citationToDisplay}
          evidenceLedger={turnLedger}
          isStreaming={isStreaming}
          messageId={messageId}
          fallbackContent={content}
          team={hasTeam ? team : undefined}
          escalationSlots={escalationSlots}
          hotTraces={hotTraces}
          stageCardTraces={stageCardTraces}
          teamPreviewTraces={teamPreviewTraces}
          toolPhases={toolPhases}
          graphAppendActKinds={graphAppendActKinds}
          graphAppendAuthorizedBy={graphAppendAuthorizedBy}
          prevExecutionIds={prevExecutionIds}
          userInterjections={userInterjections}
          turnClosed={turnClosed}
          kickoffReleased={kickoffReleased}
          onOpenBrowserLive={onOpenBrowserLive}
        />
      ) : (
        <>
          {showTeamGraph ? <TeamView {...team} /> : null}
          {reasoning ? (
            <Reasoning text={reasoning} isStreaming={isStreaming} />
          ) : null}
          {content ? (
            <Markdown
              content={content}
              citations={citations}
              citationToDisplay={citationToDisplay}
              evidenceLedger={turnLedger}
              isStreaming={isStreaming}
            />
          ) : null}
        </>
      )}
      {/* 旧 journal / 无 process：无 marker 的插话仍挂尾，避免刷新丢泡。 */}
      {unmarkedInterjections.length > 0 ? (
        <InterjectionBubbles
          items={unmarkedInterjections}
          turnClosed={turnClosed}
        />
      ) : null}
      <UnproductiveToolFailureHint
        finishReason={finishReason}
        content={content}
        process={process}
        isStreaming={isStreaming}
      />
      {citations && citations.length > 0 ? (
        <SourceCards
          items={citations}
          content={content}
          displayMap={citationDisplay}
        />
      ) : null}
      <AssistantMessageFooter
        content={content}
        process={process}
        supportIds={supportIds}
        captainContext={captainContext}
        usage={usage}
        rounds={rounds}
        costText={costText}
        durationMs={durationMs}
        clockIso={clockIso}
        finishReason={finishReason}
        failureNotice={failureNotice}
        isStreaming={isStreaming}
      />
    </>
  );
}

export { SupportDiagnosticCopyButton } from "@/components/SupportDiagnosticCopyButton";
