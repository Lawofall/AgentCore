import { pickAgentNodeIdlePrimary } from "@/components/chat/debate/debateFaceCopy";
import {
  composingWriteChars,
  toolPhaseText,
} from "@/components/chat/message-bubble/constants";
import {
  graphBadgeMuted,
  graphBadgePrimary,
} from "@/components/ui/tone-presets";
import { runningElapsedSec } from "@/lib/runningElapsed";
import { useActiveTurnPhase } from "@/stores/conversation";
import {
  projectRuntime,
  toolLabel,
  useExecutionScope,
  useExecutionStore,
} from "@/stores/execution";
import { useRunStopPendingStore } from "@/stores/runStopPending";
import {
  AlertTriangle,
  ArrowUp,
  FileText,
  Pause,
  PencilLine,
} from "lucide-react";
import { useEffect, useState } from "react";
import { isStoppableRunStatus } from "../runStopActions";
import {
  type AgentNodeData,
  type AgentNodePresentation,
  FACE_ARTIFACT_CAP,
  FACE_CARD_HEIGHT,
  basename,
  escalationKindLabel,
  isDebateAgentNode,
  statusFaceLabel,
} from "./shared";

/** Full card with task line, artifact chips, status line. */
export function AgentNodeCardFace({
  d,
  p,
  flashColor,
  flashing,
}: {
  d: AgentNodeData;
  p: AgentNodePresentation;
  flashColor: string;
  flashing: boolean;
}) {
  const isRunning = d.status === "running";
  const statusOverride = useAgentNodeStopOverride(d.runId, d.status);

  return (
    // biome-ignore lint/a11y/useSemanticElements: graph card hosts nested interactive chrome
    <div
      role="button"
      tabIndex={0}
      aria-label={
        statusOverride
          ? p.ariaLabel.replace(p.statusFace.text, statusOverride)
          : p.ariaLabel
      }
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          d.onActivate?.();
        }
      }}
      style={
        {
          "--graph-flash-color": flashColor,
          width: p.cardWidth,
          height: FACE_CARD_HEIGHT,
        } as React.CSSProperties
      }
      className={`relative cursor-pointer overflow-hidden rounded-xl border px-3 py-2.5 text-left shadow-sm outline-none ring-2 ${p.style.bg} ${p.style.ring} ${flashing ? "animate-graph-node-flash" : ""} ${
        p.highlighted
          ? "outline outline-2 outline-offset-2 outline-primary"
          : "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary/60"
      }`}
    >
      {p.revisionBadge &&
        p.revisionBadge.kind !== "debate" &&
        p.visibleFaceBadges.has("revision") && (
          <span
            className={`absolute -right-1.5 -top-1.5 z-10 flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-medium shadow-sm ring-2 ring-card ${graphBadgeMuted}`}
            title={p.revisionBadge.title}
          >
            {p.revisionBadge.kind === "hotfix" && (
              <PencilLine size={10} className="shrink-0" />
            )}
            {p.revisionBadge.label}
          </span>
        )}
      {p.handoffFace &&
        !p.revisionBadge &&
        p.visibleFaceBadges.has("handoff") && (
          <span
            className={`absolute -right-1.5 -top-1.5 z-10 flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-medium shadow-sm ring-2 ring-card ${graphBadgeMuted}`}
            title="已由新队员接手"
          >
            {p.handoffFace}
          </span>
        )}
      <AgentNodeHeader
        d={d}
        p={p}
        pulsePresence={isRunning}
        statusOverride={statusOverride}
      />
      <AgentNodeActivity d={d} p={p} showIdleTask />
      {p.artifacts.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {p.artifacts.slice(0, FACE_ARTIFACT_CAP).map((path) => (
            <span
              key={path}
              title={path}
              className="flex max-w-[120px] items-center gap-1 rounded-lg bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
            >
              <FileText size={11} className="shrink-0" />
              <span className="truncate">{basename(path)}</span>
            </span>
          ))}
          {p.artifacts.length > FACE_ARTIFACT_CAP && (
            <span className="shrink-0 rounded-lg bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              +{p.artifacts.length - FACE_ARTIFACT_CAP}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function AgentNodeHeader({
  d,
  p,
  pulsePresence,
  statusOverride,
}: {
  d: AgentNodeData;
  p: AgentNodePresentation;
  pulsePresence: boolean;
  statusOverride: string | null;
}) {
  const { color, glyph, showRoleTitle } = p.identity;
  return (
    <>
      <div className="flex items-center gap-2.5">
        <div className="relative shrink-0">
          <div
            className="flex size-7 items-center justify-center rounded-full text-sm font-semibold"
            style={{
              backgroundColor: `color-mix(in oklab, ${color} 18%, transparent)`,
              color,
            }}
          >
            {glyph}
          </div>
          <span
            className={`absolute -bottom-0.5 -right-0.5 flex size-3.5 items-center justify-center rounded-full ring-2 ring-card ${p.presence.cls} ${pulsePresence ? "animate-pulse" : ""}`}
          >
            {p.presence.icon}
          </span>
        </div>
        {showRoleTitle ? (
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
            {d.role}
          </p>
        ) : (
          <AgentNodeStatusLine d={d} p={p} statusOverride={statusOverride} />
        )}
      </div>
      <AgentNodeMeta
        d={d}
        p={p}
        statusOverride={statusOverride}
        hideStatus={!showRoleTitle}
      />
    </>
  );
}

/**
 * Honest mid-flight stop: when pending store covers this run, status line shows
 * 「停止请求中…」instead of the live elapsed face. Reuses settle cleanup from the old
 * node action bar — does not invent a parallel pending channel.
 */
function useAgentNodeStopOverride(
  runId: string,
  status: string,
): string | null {
  const messageId = useExecutionScope();
  const executionId = useExecutionStore((s) => {
    const rt = messageId ? s.byId[messageId] : undefined;
    return rt ? (projectRuntime(rt)?.id ?? null) : null;
  });
  const covered = useRunStopPendingStore((s) =>
    executionId ? s.isRunCovered(executionId, runId) : false,
  );

  useEffect(() => {
    if (!executionId) return;
    useRunStopPendingStore
      .getState()
      .clearIfSettled(executionId, runId, status);
  }, [executionId, runId, status]);

  const turnPhase = useActiveTurnPhase();
  if (covered && isStoppableRunStatus(status)) return "停止请求中…";
  if (turnPhase === "stopping" && isStoppableRunStatus(status))
    return "停止中…";
  return null;
}

/**
 * Meta 行：左侧功能 chip（含质询 / 复核 / 检查点 / 待拍板），右侧状态。
 * 默认辩手无标题时状态改挂头像行，本行只留 chip；无 chip 则整行不画。
 * 有自定义角色名时状态仍在本行（标题独占上一行，避免长名被截断）。
 */
function AgentNodeMeta({
  d,
  p,
  statusOverride,
  hideStatus,
}: {
  d: AgentNodeData;
  p: AgentNodePresentation;
  statusOverride: string | null;
  hideStatus: boolean;
}) {
  const showEscalationPending =
    (d.escalationPending ?? 0) > 0 && p.visibleFaceBadges.has("escalation");
  const showCrossExam =
    d.debateCrossExamMark?.mode === "suffix" &&
    d.onActivateCrossExam != null &&
    p.visibleFaceBadges.has("crossExam");
  const showRaised =
    (d.escalationPending ?? 0) === 0 &&
    (d.escalationRaised ?? 0) > 0 &&
    Boolean(d.escalationKind) &&
    d.escalationKind !== "normal" &&
    p.visibleFaceBadges.has("escalation");
  const hasChips =
    showCrossExam ||
    Boolean(p.reviewConcernFace && p.visibleFaceBadges.has("reviewConcern")) ||
    Boolean(p.checkpointFace && p.visibleFaceBadges.has("checkpoint")) ||
    showEscalationPending ||
    showRaised;
  if (hideStatus && !hasChips) return null;
  return (
    <div className="mt-1 flex max-h-10 flex-wrap items-center gap-1.5 overflow-hidden text-xs text-muted-foreground">
      {showCrossExam && d.debateCrossExamMark && d.onActivateCrossExam && (
        <CrossExamMarkButton
          mark={d.debateCrossExamMark}
          onActivate={d.onActivateCrossExam}
        />
      )}
      {p.reviewConcernFace && p.visibleFaceBadges.has("reviewConcern") && (
        <span
          className={`flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-1.5 py-0.5 font-medium ${p.reviewConcernFace.cls}`}
        >
          <AlertTriangle size={10} />
          {p.reviewConcernFace.label}
        </span>
      )}
      {p.checkpointFace && p.visibleFaceBadges.has("checkpoint") && (
        <span
          className={`flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-1.5 py-0.5 font-medium ${p.checkpointFace.cls}`}
        >
          <Pause size={10} />
          {p.checkpointFace.label}
        </span>
      )}
      {showEscalationPending && (
        <span className={`${graphBadgePrimary} whitespace-nowrap`}>
          <ArrowUp size={10} />
          待你拍板
          {d.escalationKind && d.escalationKind !== "normal"
            ? ` · ${escalationKindLabel(d.escalationKind)}`
            : ""}
          {(d.escalationPending ?? 0) > 1 ? ` ${d.escalationPending}` : ""}
        </span>
      )}
      {showRaised && (
        <span className={`${graphBadgeMuted} whitespace-nowrap`}>
          <ArrowUp size={10} />
          {escalationKindLabel(d.escalationKind ?? undefined)}
        </span>
      )}
      {!hideStatus && (
        <AgentNodeStatusLine d={d} p={p} statusOverride={statusOverride} />
      )}
    </div>
  );
}

/**
 * Live elapsed seconds since the run's REAL backend start (`startedAt`, epoch ms from
 * the `run_started` frame's wall-clock `t`) — NOT since component mount. Deriving from
 * `startedAt` makes the「执行中 · 用时」counter survive node remount (视口虚拟化 / 幕 LOD
 * 切换 / 重新布局), late viewing (晚开协作图), and reload — all of which used to reset it
 * to 0. The 1s ticker only forces a re-render; the value is recomputed from the wall
 * clock each render. Clamped to ≥0 (guards client/server clock skew); on completion the
 * authoritative `durationMs` takes over. Returns 0 when not ticking or start is unknown.
 */
function useRunningElapsed(
  ticking: boolean,
  startedAt: number | null | undefined,
): number {
  const [, force] = useState(0);
  useEffect(() => {
    if (!ticking) return;
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [ticking]);
  if (!ticking || startedAt == null) return 0;
  return runningElapsedSec(startedAt);
}

/** 质询标记：可点直达该轮质询 run。suffix「含质询」走链接色、replace「质询作答失败」走告警色。 */
function CrossExamMarkButton({
  mark,
  onActivate,
}: {
  mark: { label: string; mode: "suffix" | "replace" };
  onActivate: () => void;
}) {
  return (
    <button
      type="button"
      className={
        mark.mode === "replace"
          ? "shrink-0 whitespace-nowrap text-destructive hover:underline focus-visible:underline focus-visible:outline-none"
          : "shrink-0 whitespace-nowrap text-primary hover:underline focus-visible:underline focus-visible:outline-none"
      }
      aria-label={
        mark.mode === "replace" ? "查看质询作答详情" : "查看本轮质询作答详情"
      }
      onClick={(e) => {
        e.stopPropagation();
        onActivate();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") e.stopPropagation();
      }}
    >
      {mark.label}
    </button>
  );
}

function AgentNodeStatusLine({
  d,
  p,
  statusOverride,
}: {
  d: AgentNodeData;
  p: AgentNodePresentation;
  statusOverride: string | null;
}) {
  const elapsed = useRunningElapsed(
    p.statusFace.tickElapsed && statusOverride == null,
    d.startedAt,
  );
  const face = statusFaceLabel(
    d.status,
    d.durationMs,
    elapsed,
    d.debateRoundPhase,
    d.group === "debate:witness" && d.continuesRunId == null,
    d.error,
    d.status === "running" ? (d.phase ?? null) : null,
    d.phaseTool,
    d.failureKind,
    d.productLanded,
  );
  const mark = d.debateCrossExamMark;

  // replace 态（质询作答失败）整行归因、可点直达质询 run；
  // suffix「含质询」在 meta 行，状态行不再拼后缀。
  // 停止请求覆盖优先于正常进行中用时，不覆盖质询失败归因行。
  if (mark?.mode === "replace" && d.onActivateCrossExam) {
    return (
      <p
        className={`ml-auto shrink-0 whitespace-nowrap text-xs tabular-nums leading-snug ${face.cls}`}
      >
        <CrossExamMarkButton mark={mark} onActivate={d.onActivateCrossExam} />
      </p>
    );
  }

  return (
    <p
      className={`ml-auto shrink-0 whitespace-nowrap text-xs tabular-nums leading-snug ${face.cls}`}
    >
      {statusOverride ?? face.text}
    </p>
  );
}

function AgentNodeActivity({
  d,
  p,
  showIdleTask,
}: {
  d: AgentNodeData;
  p: AgentNodePresentation;
  showIdleTask: boolean;
}) {
  if (p.liveTool) {
    const charLabel = composingWriteChars(
      p.liveTool.toolName,
      p.liveTool.chars,
    );
    return (
      <p className="mt-2 line-clamp-2 text-xs leading-snug text-primary/90">
        {toolLabel(p.liveTool.toolName)}
        {charLabel && (
          <span className="text-muted-foreground/70">
            {" · "}
            {charLabel}
          </span>
        )}
        <span className="ml-0.5 inline-block animate-pulse text-primary">
          ▋
        </span>
      </p>
    );
  }
  if (p.liveToolExec) {
    const phaseLabel = toolPhaseText(p.liveToolExec.phase) ?? "Working";
    return (
      <p className="mt-2 line-clamp-2 text-xs leading-snug text-primary/90">
        {phaseLabel} · {toolLabel(p.liveToolExec.toolName)}
        <span className="ml-0.5 inline-block animate-pulse text-primary">
          ▋
        </span>
      </p>
    );
  }
  if (p.livePreview) {
    return (
      <p className="mt-2 line-clamp-2 text-xs leading-snug text-muted-foreground/80">
        {p.livePreview}
        <span className="ml-0.5 inline-block animate-pulse text-primary">
          ▋
        </span>
      </p>
    );
  }
  if (p.liveThinking) {
    return (
      <p className="mt-2 line-clamp-2 text-xs italic leading-snug text-muted-foreground/60">
        {p.liveThinking}
        <span className="ml-0.5 inline-block animate-pulse text-primary">
          ▋
        </span>
      </p>
    );
  }
  if (showIdleTask && p.revisionFaceHint) {
    return (
      <p className="mt-2 line-clamp-2 text-xs leading-snug text-muted-foreground/70">
        {p.revisionFaceHint}
      </p>
    );
  }
  if (!showIdleTask) return null;

  const primary = pickAgentNodeIdlePrimary({
    status: d.status,
    outputPreview: d.outputPreview,
    task: d.task,
    isDebate: isDebateAgentNode(d),
    debateFacePrimary: d.debateFacePrimary,
  });
  const challenge = d.challengePreview?.trim() || null;
  if (!primary && !challenge) return null;

  return (
    <div className="mt-2 space-y-0.5">
      {primary && (
        <p className="line-clamp-2 text-xs leading-snug text-muted-foreground/70">
          {primary}
        </p>
      )}
      {challenge && (
        <p className="line-clamp-1 text-xs leading-snug text-muted-foreground/55">
          被盯 · {challenge}
        </p>
      )}
    </div>
  );
}
