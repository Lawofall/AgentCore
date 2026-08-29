import { toolPhaseText } from "@/components/chat/message-bubble/constants";
import { isWitnessSeatRun } from "@/components/graph/helpers";
import { statusPillSoft } from "@/components/ui/tone-presets";
import { formatCompact, formatDuration } from "@/lib/format";
import { STANCE_META, toolLabel } from "@/stores/execution";
import { visibleFaceBadgeKeys } from "./faceBudget";
import {
  type AgentNodeData,
  type AgentNodePresentation,
  PRESENCE_STYLES,
  STATUS_STYLES,
  buildRevisionBadge,
  checkpointBadge,
  escalationKindLabel,
  failureDetailSentence,
  isDebateAgentNode,
  revisedBadge,
  revisionFaceHint,
  statusFaceLabel,
} from "./shared";

export function buildAgentNodePresentation(
  d: AgentNodeData,
): AgentNodePresentation {
  const style = STATUS_STYLES[d.status] ?? STATUS_STYLES.pending;
  const presence = PRESENCE_STYLES[d.status] ?? PRESENCE_STYLES.pending;
  const artifacts = d.artifacts ?? [];
  const isRunning = d.status === "running";
  const phase = isRunning ? (d.phase ?? null) : null;
  // waiting_children / winding_down must not fall through to residual reasoning
  // as「思考中」— phase is the activity source of truth while running.
  const suppressThinkingPreview =
    phase === "waiting_children" || phase === "winding_down";
  const liveTool = isRunning ? (d.toolProgress ?? null) : null;
  const liveToolExec =
    isRunning && !liveTool ? (d.toolExecutionLive ?? null) : null;
  const livePreview =
    isRunning && !liveTool && !liveToolExec ? d.outputPreview : "";
  const liveThinking =
    isRunning &&
    !liveTool &&
    !liveToolExec &&
    !livePreview &&
    !suppressThinkingPreview
      ? (d.reasoningPreview ?? "")
      : "";
  const cardWidth = 210;
  const enterDelay = Math.min((d.enterIndex ?? 0) * 35, 280);

  const modelText = d.model ?? "—";
  const tokenText =
    d.realTokens && d.realTokens > 0
      ? formatCompact(d.realTokens)
      : d.tokenCount > 0
        ? `≈${formatCompact(d.tokenCount)}`
        : "—";
  const durationText = d.durationMs ? formatDuration(d.durationMs) : null;
  const witnessSeat = isWitnessSeatRun({
    group: d.group,
    continuesRunId: d.continuesRunId,
  });
  const baseFace = statusFaceLabel(
    d.status,
    d.durationMs,
    undefined,
    d.debateRoundPhase,
    witnessSeat,
    d.error,
    phase,
    d.phaseTool,
    d.failureKind,
    d.productLanded,
  );
  const statusFace =
    d.debateCrossExamMark?.mode === "replace"
      ? { ...baseFace, text: d.debateCrossExamMark.label }
      : d.debateCrossExamMark?.mode === "suffix"
        ? {
            ...baseFace,
            text: `${baseFace.text} · ${d.debateCrossExamMark.label}`,
          }
        : baseFace;
  const debate = isDebateAgentNode(d);
  const revisionBadge = buildRevisionBadge({
    isRevision: d.isRevision,
    continuationIndex: d.continuationIndex,
    round: d.round,
    isDebate: debate,
    beat: d.debateBeat,
  });
  const faceHint =
    d.isRevision && !debate ? revisionFaceHint(d.revisionSummary) : null;

  const ariaLabel = `${d.role}，${statusFace.text.replace(/ · \d+s$/, "")}，模型 ${modelText}，Token ${tokenText}${
    d.costText ? `，成本 ${d.costText}` : ""
  }${durationText ? `，用时 ${durationText}` : ""}${
    d.toolCount > 0 ? `，工具 ${d.toolCount} 次` : ""
  }${artifacts.length > 0 ? `，产物 ${artifacts.length} 个` : ""}${
    d.revised ? `，${revisedBadge(d.revised).label}` : ""
  }${d.replacesRunId ? "，接手" : ""}${
    revisionBadge
      ? `，${revisionBadge.kind === "debate" ? revisionBadge.label : `接续 ${revisionBadge.label}`}`
      : ""
  }${d.checkpoint ? `，检查点${checkpointBadge(d.checkpoint).label}` : ""}${
    (d.escalationPending ?? 0) > 0
      ? `，${d.escalationPending} 项待你拍板${d.escalationKind && d.escalationKind !== "normal" ? `（${escalationKindLabel(d.escalationKind)}）` : ""}`
      : (d.escalationRaised ?? 0) > 0
        ? `，上报 ${d.escalationRaised} 条${d.escalationKind && d.escalationKind !== "normal" ? `（${escalationKindLabel(d.escalationKind)}）` : ""}`
        : ""
  }${
    // a11y 不回归：即便视觉预算挤掉了「方向风险 / 待关注 / 含质询」，仍进 aria 播报。
    d.reviewConcern === "critical"
      ? "，方向风险"
      : d.reviewConcern === "warning"
        ? "，待关注"
        : ""
  }${d.debateCrossExamMark ? `，${d.debateCrossExamMark.label}` : ""}`;

  const peekActivity = liveTool
    ? {
        heading: "正在生成",
        text: `${toolLabel(liveTool.toolName)}${
          liveTool.chars > 0 ? ` · ${formatCompact(liveTool.chars)} 字` : ""
        }`,
      }
    : liveToolExec
      ? {
          heading: toolPhaseText(liveToolExec.phase) ?? "Working",
          text: toolLabel(liveToolExec.toolName),
        }
      : livePreview
        ? { heading: "输出中", text: livePreview }
        : liveThinking
          ? { heading: "思考中", text: liveThinking, italic: true }
          : d.status === "failed" && d.error
            ? {
                heading: "失败原因",
                // d.error is engine/infra text (str(exception), 结构闸 paths…) — same
                // curation as FailureStrip, never the raw string.
                text: failureDetailSentence(d.failureKind, d.productLanded),
              }
            : d.outputPreview
              ? { heading: "产出预览", text: d.outputPreview }
              : null;

  const peekTags: string[] = [];
  if (d.stance) peekTags.push(STANCE_META[d.stance].label);
  if (d.isSubtask) peekTags.push("子任务");
  if (revisionBadge) {
    peekTags.push(
      revisionBadge.kind === "debate"
        ? revisionBadge.label
        : `接续 ${d.role} 的现场 · ${revisionBadge.label}`,
    );
  }
  if (d.revised) peekTags.push(revisedBadge(d.revised).label);
  if (d.replacesRunId) peekTags.push("接手");
  if (d.checkpoint) peekTags.push(checkpointBadge(d.checkpoint).label);
  if (d.reviewConcern === "critical") peekTags.push("方向风险");
  else if (d.reviewConcern === "warning") peekTags.push("待关注");
  // 含质询 / 质询作答失败标记：被 face 预算挤出时仍在 hover peek 可达。
  if (d.debateCrossExamMark) peekTags.push(d.debateCrossExamMark.label);
  if ((d.escalationPending ?? 0) > 0) {
    const kindTag =
      d.escalationKind && d.escalationKind !== "normal"
        ? escalationKindLabel(d.escalationKind)
        : null;
    peekTags.push(
      `待你拍板${(d.escalationPending ?? 0) > 1 ? ` ${d.escalationPending}` : ""}${kindTag ? ` · ${kindTag}` : ""}`,
    );
  } else if ((d.escalationRaised ?? 0) > 0) {
    const kindTag =
      d.escalationKind && d.escalationKind !== "normal"
        ? escalationKindLabel(d.escalationKind)
        : null;
    peekTags.push(
      `上报${(d.escalationRaised ?? 0) > 1 ? ` ${d.escalationRaised}` : ""}${kindTag ? ` · ${kindTag}` : ""}`,
    );
  }

  // pending / stop / resolved(放行|调整) 均走 face；文案由 checkpointBadge 归一。
  const checkpointFace = d.checkpoint ? checkpointBadge(d.checkpoint) : null;

  const reviewConcernFace =
    d.reviewConcern === "critical"
      ? { label: "方向风险", cls: statusPillSoft.destructive }
      : d.reviewConcern === "warning"
        ? { label: "待关注", cls: "bg-warning/10 text-warning" }
        : null;

  // face 徽标预算（决策 3）：功能徽标同屏 ≤2，优先级 待拍板 > 异常 > 过程性。
  // 立场（身份层）与状态行（用时层）不计预算、恒显。
  const visibleFaceBadges = visibleFaceBadgeKeys({
    escalationPending: d.escalationPending ?? 0,
    escalationRaised: d.escalationRaised ?? 0,
    escalationKind: d.escalationKind,
    checkpointPending:
      checkpointFace != null && d.checkpoint?.status === "pending",
    checkpointStopped:
      checkpointFace != null && d.checkpoint?.decision === "stop",
    checkpointReleased:
      checkpointFace != null &&
      d.checkpoint?.status === "resolved" &&
      d.checkpoint?.decision !== "stop",
    reviewConcern: reviewConcernFace != null,
    revision: revisionBadge != null && revisionBadge.kind !== "debate",
    handoff: d.replacesRunId != null,
    crossExamSuffix: d.debateCrossExamMark?.mode === "suffix",
  });

  return {
    style,
    presence,
    artifacts,
    liveTool,
    liveToolExec,
    livePreview,
    liveThinking,
    highlighted: d.focused,
    cardWidth,
    enterDelay,
    modelText,
    tokenText,
    durationText,
    ariaLabel,
    peekActivity,
    peekTags,
    checkpointFace,
    reviewConcernFace,
    statusFace,
    revisionBadge,
    revisionFaceHint: faceHint,
    handoffFace: d.replacesRunId ? "接手" : null,
    visibleFaceBadges,
  };
}
