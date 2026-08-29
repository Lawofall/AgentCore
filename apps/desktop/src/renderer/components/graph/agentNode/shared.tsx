import { statusPillSoft } from "@/components/ui/tone-presets";
import { formatDuration } from "@/lib/format";
import { NODE_HEIGHT } from "@/lib/graphMetrics";
import type { ReviewConcernLevel } from "@/lib/reviewConcern";
import type {
  DebateBeat,
  PlanRevisionKind,
  RunCheckpoint,
  RunStatus,
  Stance,
} from "@/stores/execution";
import {
  debateBeatLabel,
  isDebateTaggedRun,
  runPhaseLabel,
  runStatusLabel,
  toolLabel,
} from "@/stores/execution";
import type { WorkerRunPhase } from "@/stores/execution";
import { Check, Loader2, X } from "lucide-react";
import type { FaceBadgeKey } from "./faceBudget";

export interface AgentNodeData {
  agentId: string;
  role: string;
  runId: string;
  status: RunStatus;
  isAnimating: boolean;
  task: string;
  outputPreview: string;
  /**
   * 辩论续轮卡片主文：`round_focus` / 升格 task 块首句。
   * 完成态优先 {@link outputPreview}；无产出时用此替代开团 role 模板。
   */
  debateFacePrimary?: string | null;
  /** 被驳命门一行副标题（`channel=challenge`）。 */
  challengePreview?: string | null;
  reasoningPreview?: string;
  toolProgress?: { toolName: string; chars: number } | null;
  /** Worker tool EXECUTION phase (transport-only `tool_use_progress` with run_id). */
  toolExecutionLive?: { toolName: string; phase: string } | null;
  /** Worker mid-flight activity phase (`run_phase`); drives face copy when running. */
  phase?: WorkerRunPhase | null;
  phaseTool?: string | null;
  tokenCount: number;
  toolCount: number;
  artifacts?: string[];
  focused: boolean;
  nodeWidth?: number;
  model?: string | null;
  durationMs?: number | null;
  /** 真实开始时间（epoch ms）：进行中「执行中 · Ns」live 计时锚点（见 RunNode.startedAt）。 */
  startedAt?: number | null;
  realTokens?: number;
  costText?: string;
  handleDirection?: "vertical" | "horizontal";
  isSubtask?: boolean;
  isRevision?: boolean;
  /** 接续序号（1-based）；角标「续 ×N」。 */
  continuationIndex?: number;
  continuesRunId?: string | null;
  /** 真·多轮辩论轮次（1-based；0 = 非多轮）。与侧栏接续链同源。 */
  round?: number;
  /**
   * 辩论 continue_run 发言角色（陈词 / 质询 / 结辩）。来自 `run_context.channel`；
   * 缺省按陈词。协作图质询已折进轮节点，角标仅陈词续轮「第 N 轮」/ 结辩「结辩」；
   * 质询态见 {@link debateRoundPhase}。
   */
  debateBeat?: DebateBeat | null;
  /**
   * 轮节点折叠质询后的直播进度文案（「立论中」/「质询作答中」）；无折叠或非运行中为 null。
   */
  debateRoundPhase?: string | null;
  /**
   * 收场态质询标记：完成「含质询」后缀 / 质询失败整行归因；可点直达质询 run。
   */
  debateCrossExamMark?: {
    label: string;
    mode: "suffix" | "replace";
  } | null;
  /** 点击质询标记 → activateNode(质询 runId)；与整卡 onActivate 分路。 */
  onActivateCrossExam?: () => void;
  /** 辩论配对组（`debate:*`）；与 stance 一起判定辩手 / 续轮。 */
  group?: string | null;
  /**
   * 热修 / 续派改点摘要：来自 `run_context` channel=`continuation` 的 body。
   * 缺省时卡片面回退到继承的原 task。
   */
  revisionSummary?: string | null;
  revised?: PlanRevisionKind | null;
  /** 回落换人：接手的原 run id。 */
  replacesRunId?: string | null;
  stance?: Stance | null;
  checkpoint?: RunCheckpoint | null;
  escalationPending?: number;
  escalationRaised?: number;
  /** 节点上最严重的升级展示类（真 scope > 需求矛盾 > dep > normal）。 */
  escalationKind?: EscalationDisplayKind | null;
  /** Review/QC output flagged by {@link detectReviewConcern} (中间可见性 phase-1). */
  reviewConcern?: ReviewConcernLevel | null;
  /** Failure reason from `run_failed` — drives face「模型中断/调用失败」+ peek. */
  error?: string | null;
  /** `run_failed.failure_kind` — preferred face class over error-text heuristics. */
  failureKind?: import("@/types/events").RunFailureKind | null;
  /** `run_failed.product_landed` — files already on disk before failure. */
  productLanded?: boolean | null;
  /** Folded child runs under this unit root (delegation drill-in). */
  foldedChildCount?: number;
  unitExpanded?: boolean;
  onToggleUnitExpand?: () => void;
  enterIndex?: number;
  onActivate?: () => void;
  [key: string]: unknown;
}

export const FACE_ARTIFACT_CAP = 2;
export const PEEK_ARTIFACT_CAP = 6;

/**
 * Fixed face card height — equals layout {@link NODE_HEIGHT}. Content clips /
 * scrolls inside; never grow the RF footprint past the ELK slot.
 */
export const FACE_CARD_HEIGHT = NODE_HEIGHT;

export const STATUS_STYLES: Record<string, { ring: string; bg: string }> = {
  pending: { ring: "ring-muted-foreground/30", bg: "bg-card" },
  running: { ring: "ring-primary", bg: "bg-card" },
  completed: { ring: "ring-success", bg: "bg-card" },
  failed: { ring: "ring-destructive", bg: "bg-card" },
  cancelled: { ring: "ring-muted-foreground/30", bg: "bg-muted" },
  skipped: { ring: "ring-muted-foreground/30", bg: "bg-muted" },
};

export const PRESENCE_STYLES: Record<
  string,
  { cls: string; icon: React.ReactNode | null }
> = {
  pending: { cls: "bg-muted-foreground/50", icon: null },
  running: {
    cls: "bg-primary",
    icon: <Loader2 size={9} className="animate-spin text-primary-foreground" />,
  },
  completed: {
    cls: "bg-success",
    icon: (
      <Check size={9} strokeWidth={3} className="text-success-foreground" />
    ),
  },
  failed: {
    cls: "bg-destructive",
    icon: (
      <X size={9} strokeWidth={3} className="text-destructive-foreground" />
    ),
  },
  cancelled: { cls: "bg-muted-foreground/50", icon: null },
  skipped: { cls: "bg-muted-foreground/50", icon: null },
};

export function basename(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  return cut >= 0 ? trimmed.slice(cut + 1) : trimmed;
}

export function statusLabel(status: RunStatus): string {
  return runStatusLabel(status);
}

/**
 * Short face label for a failed worker (quality / format / model interrupt / call).
 * Prefer machine-readable ``failureKind``; keep a thin error-text fallback for old journals.
 * Full `error` stays in peek / run detail. Do not regex-guess format vs quality from error text.
 */
export function failureFaceLabel(
  error: string | null | undefined,
  failureKind?: import("@/types/events").RunFailureKind | null,
  productLanded?: boolean | null,
): string {
  // Files already on disk before the terminal failure — don't imply empty failure.
  if (productLanded) return "产出已落盘";
  if (failureKind === "quality") return "未达标";
  if (failureKind === "format") return "格式未过";
  if (failureKind === "model") return "模型中断";
  if (failureKind === "call") return "调用失败";
  const raw = (error ?? "").trim();
  if (!raw) return "调用失败";
  if (
    /中断|abort|断开|停滞|stalled|收尾时中断|degraded_handoff|降级交接/i.test(
      raw,
    )
  ) {
    return "模型中断";
  }
  if (
    /模型|llm|调用|timeout|超时|invalid_request|upstream|网络|provider|api\s*error/i.test(
      raw,
    )
  ) {
    return "调用失败";
  }
  return "失败";
}

/**
 * User-facing failure sentence — {@link failureFaceLabel} one level down (chip label vs
 * the detail line under it).
 *
 * `run.error` is a **model** face: on the infra paths it is `str(exception)` and on the
 * contract paths it names engine gates (「结构闸：缺少 audit JSON 产物：…」), which reads to
 * the user as if *they* forgot to hand something in. So curate by the machine-readable
 * `failureKind` and never render the raw text; it stays in logs / run detail for us.
 */
export function failureDetailSentence(
  failureKind?: import("@/types/events").RunFailureKind | null,
  productLanded?: boolean | null,
): string {
  if (productLanded) {
    return "中途出错了，不过已经生成的文件都保留了下来。";
  }
  switch (failureKind) {
    case "quality":
      return "产出没有达到要求，没有采用。可以让我重做这一部分。";
    case "format":
      return "产出的格式不符合要求，没有采用。可以让我重做这一部分。";
    case "model":
      return "模型响应中断，这一步没有完成。可以让我重试。";
    case "call":
      return "调用出错，这一步没有完成。可以让我重试。";
    default:
      return "这一步没有完成。可以让我重试。";
  }
}

/** Face status line for parallel wave visibility (排队 / 执行 / 完成用时 / 失败). */
export function statusFaceLabel(
  status: RunStatus,
  durationMs: number | null | undefined,
  elapsedSec?: number,
  /** 辩论轮节点折叠质询后的进度覆盖（立论中 / 质询作答中）。 */
  debateRoundPhase?: string | null,
  /**
   * 证人席位根节点：开赛挂席、主持人点名才作答。
   * pending→待命（非排队）；skipped→未传唤（整场/质询结束仍未点名）。
   */
  witnessSeat?: boolean,
  /** `run_failed.error` — humanizes the failed face line. */
  error?: string | null,
  /** Worker mid-flight activity phase — preferred over bare「执行中」when running. */
  phase?: WorkerRunPhase | null,
  phaseTool?: string | null,
  /** `run_failed.failure_kind` — preferred over error-text heuristics. */
  failureKind?: import("@/types/events").RunFailureKind | null,
  /** `run_failed.product_landed` — files already on disk before failure. */
  productLanded?: boolean | null,
): { text: string; cls: string; tickElapsed: boolean } {
  if (debateRoundPhase && status === "running") {
    const suffix =
      elapsedSec !== undefined && elapsedSec >= 1 ? ` · ${elapsedSec}s` : "";
    return {
      text: `${debateRoundPhase}${suffix}`,
      cls: "text-primary/90",
      tickElapsed: true,
    };
  }
  if (witnessSeat && status === "pending") {
    return {
      text: "待命",
      cls: "text-muted-foreground",
      tickElapsed: false,
    };
  }
  if (witnessSeat && status === "skipped") {
    return {
      text: "未传唤",
      cls: "text-muted-foreground/70",
      tickElapsed: false,
    };
  }
  switch (status) {
    case "pending":
      return {
        text: "排队中",
        cls: "text-muted-foreground",
        tickElapsed: false,
      };
    case "running": {
      const suffix =
        elapsedSec !== undefined && elapsedSec >= 1 ? ` · ${elapsedSec}s` : "";
      const phaseText = runPhaseLabel(phase, phaseTool, toolLabel);
      return {
        text: `${phaseText ?? "执行中"}${suffix}`,
        cls: "text-primary/90",
        tickElapsed: true,
      };
    }
    case "completed": {
      const dur = durationMs ? formatDuration(durationMs) : null;
      return {
        text: dur ? `已完成 · ${dur}` : "已完成",
        cls: "text-muted-foreground",
        tickElapsed: false,
      };
    }
    case "failed":
      return {
        text: failureFaceLabel(error, failureKind, productLanded),
        cls: productLanded ? "text-warning" : "text-destructive",
        tickElapsed: false,
      };
    case "cancelled":
      return {
        text: runStatusLabel("cancelled"),
        cls: "text-muted-foreground",
        tickElapsed: false,
      };
    case "skipped":
      return {
        text: "未执行",
        cls: "text-muted-foreground",
        tickElapsed: false,
      };
    default:
      return {
        text: statusLabel(status),
        cls: "text-muted-foreground",
        tickElapsed: false,
      };
  }
}

/** 热修 / 续派角标文案（续 ×N）；非接续不挂角标。 */
export function revisionVersionBadge(
  continuationIndex: number | undefined,
): string | null {
  if (!continuationIndex || continuationIndex < 1) return null;
  return `续 ×${continuationIndex}`;
}

/** 与 {@link isDebateParticipantRun} 同判定：stance 或显式辩形态 / 证人席 group。 */
export function isDebateAgentNode(
  d: Pick<AgentNodeData, "stance" | "group">,
): boolean {
  return isDebateTaggedRun(d);
}

/** 从 `run_context` 的 continuation 通道抽出改点正文（唤回 / 续派指令）。 */
export function revisionFeedbackSummary(
  blocks: ReadonlyArray<{ channel: string; body: string }> | null | undefined,
): string | null {
  if (!blocks?.length) return null;
  const block = blocks.find((b) => b.channel === "continuation");
  const text = block?.body?.trim().replace(/\s+/g, " ");
  return text || null;
}

/** 热修 / 续派卡片面一行：优先「按指示：改点」，避免只重复原 task。 */
export function revisionFaceHint(
  summary: string | null | undefined,
): string | null {
  if (!summary) return null;
  return `按指示：${summary}`;
}

export type RevisionBadgeKind = "hotfix" | "debate";

export interface RevisionBadgePresentation {
  kind: RevisionBadgeKind;
  /** 角标可见文案：`续 ×1` / `第 2 轮` / `结辩`。 */
  label: string;
  /** tooltip / title。 */
  title: string;
}

/**
 * 协作图接续角标：multi_agent = 「续 ×N」；辩论可见列按 beat——续轮陈词
 * 「第 N 轮」、结辩「结辩」。质询已折进轮节点，图上不再挂「第 N 轮·质询」。
 * 非接续不挂角标。
 */
export function buildRevisionBadge(opts: {
  isRevision?: boolean;
  continuationIndex?: number;
  round?: number;
  isDebate: boolean;
  beat?: DebateBeat | null;
}): RevisionBadgePresentation | null {
  const idx =
    opts.continuationIndex && opts.continuationIndex > 0
      ? opts.continuationIndex
      : 0;
  if (!opts.isRevision || idx < 1) return null;
  if (opts.isDebate) {
    // 协作图节点不会是折叠拍；若误传入则不挂角标（态在轮内 phase）。
    if (
      opts.beat === "cross_exam" ||
      opts.beat === "rebuttal" ||
      opts.beat === "crux"
    ) {
      return null;
    }
    const label = debateBeatLabel({
      round: opts.round,
      continuationIndex: opts.continuationIndex,
      beat: opts.beat,
    });
    return {
      kind: "debate",
      label,
      title: label,
    };
  }
  const v = `续 ×${idx}`;
  return {
    kind: "hotfix",
    label: v,
    title: `同人接续 ${v}`,
  };
}

export function revisedBadge(kind: PlanRevisionKind): {
  label: string;
  hint: string;
} {
  if (kind === "bind") {
    return { label: "职责已定稿", hint: "CEO 据上游产出定稿了这一步的职责" };
  }
  return { label: "方向已校准", hint: "CEO 据中途发现调整了这一步的方向" };
}

export type EscalationDisplayKind =
  | "normal"
  | "scope"
  | "dep"
  | "contradiction";

/**
 * Legacy-tape display: older Gate runs mapped contradiction → wire `kind=scope`
 * with「需求矛盾」in the question. Live Gate no longer does that; keep this so
 * old recordings still label honestly instead of「职责偏离」.
 */
export function isContradictionEscalation(e: {
  kind?: string;
  question?: string;
}): boolean {
  if (e.kind != null && e.kind !== "scope") return false;
  return /需求矛盾|存在矛盾|互相矛盾|conflicting\s+requirements/i.test(
    e.question ?? "",
  );
}

export function escalationKindLabel(
  kind: EscalationDisplayKind | undefined,
): string {
  if (kind === "contradiction") return "需求矛盾";
  if (kind === "scope") return "职责偏离";
  if (kind === "dep") return "缺输入";
  return "普通";
}

/** Label for one escalation row (prefer question-side contradiction over wire scope). */
export function escalationRowKindLabel(esc: {
  kind?: "normal" | "scope" | "dep";
  question?: string;
}): string | null {
  if (isContradictionEscalation(esc)) return "需求矛盾";
  if (!esc.kind || esc.kind === "normal") return null;
  return escalationKindLabel(esc.kind);
}

/** Pick the most severe escalate kind on a run (true scope > contradiction > dep > normal). */
export function pickEscalationKind(
  escalations: { kind?: "normal" | "scope" | "dep"; question?: string }[],
): EscalationDisplayKind | null {
  if (escalations.length === 0) return null;
  const scopeOnes = escalations.filter((e) => e.kind === "scope");
  if (scopeOnes.some((e) => !isContradictionEscalation(e))) return "scope";
  if (scopeOnes.some((e) => isContradictionEscalation(e)))
    return "contradiction";
  if (escalations.some((e) => e.kind === "dep")) return "dep";
  return "normal";
}

export function checkpointBadge(c: RunCheckpoint): {
  label: string;
  cls: string;
} {
  if (c.status === "pending") {
    return { label: "待放行", cls: statusPillSoft.primary };
  }
  if (c.decision === "stop") {
    return {
      label: runStatusLabel("cancelled"),
      cls: statusPillSoft.destructive,
    };
  }
  if (c.decision === "adjust") {
    return { label: "已调整", cls: statusPillSoft.muted };
  }
  return { label: "已放行", cls: statusPillSoft.muted };
}

export interface AgentNodePresentation {
  style: { ring: string; bg: string };
  presence: { cls: string; icon: React.ReactNode | null };
  artifacts: string[];
  liveTool: { toolName: string; chars: number } | null;
  liveToolExec: { toolName: string; phase: string } | null;
  livePreview: string;
  liveThinking: string;
  highlighted: boolean;
  cardWidth: number;
  enterDelay: number;
  modelText: string;
  tokenText: string;
  durationText: string | null;
  ariaLabel: string;
  peekActivity: { heading: string; text: string; italic?: boolean } | null;
  peekTags: string[];
  checkpointFace: { label: string; cls: string } | null;
  reviewConcernFace: { label: string; cls: string } | null;
  statusFace: { text: string; cls: string; tickElapsed: boolean };
  /** 热修 vN / 辩论「第 N 轮」角标；null = 不挂。 */
  revisionBadge: RevisionBadgePresentation | null;
  /** 热修 V2 空闲态卡片面优先行（按指示：…）；辩论 / 无改点时为 null。 */
  revisionFaceHint: string | null;
  handoffFace: string | null;
  /**
   * face 徽标预算（批 R3 决策 3）：本 face 该显示哪几枚功能徽标（同屏 ≤2，
   * 优先级 待拍板 > 异常 > 过程性）。身份类（立场）不在内、恒显。被挤出的
   * 标记不进此集合，但仍在 {@link ariaLabel} 与 hover peek 里可达（a11y 不回归）。
   */
  visibleFaceBadges: ReadonlySet<FaceBadgeKey>;
}
