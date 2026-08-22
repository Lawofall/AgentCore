/**
 * Shared decision-card meta for ask_user + team_preview presentation.
 *
 * One table per family variant (intent / primitive) — replaces the former parallel
 * INTENT_CONFIG (CheckpointCard) and RESOLVED_META_* (TeamPreviewCard). Kind / wire
 * contracts stay untouched; this is display copy + icons only.
 */
import type { resolvedCheckpointTone } from "@/components/ui/tone-presets";
import type { KickoffPrimitive } from "@/stores/conversation";
import type { CheckpointDecision, CheckpointIntent } from "@/types/events";
import {
  Ban,
  BookOpenCheck,
  Check,
  CircleHelp,
  Clock,
  FolderTree,
  Layers,
  type LucideIcon,
  OctagonX,
  Pencil,
  Scale,
  ShieldAlert,
} from "lucide-react";

export type ResolvedToneKey = keyof typeof resolvedCheckpointTone;

export type AskIntentMeta = {
  icon: LucideIcon;
  activeCaption: string;
  cta: string;
  ctaIcon: LucideIcon;
  showFooterHint: boolean;
  resolved: Record<
    CheckpointDecision,
    { label: string; tone: ResolvedToneKey }
  >;
};

/** Decision → icon for ask_user settled stubs (tone comes from intent.resolved). */
export const ASK_RESOLVED_DECISION_ICON = {
  continue: Check,
  adjust: Pencil,
  stop: OctagonX,
  research_first: OctagonX,
  timeout: Clock,
  orphaned: Ban,
} as const satisfies Record<CheckpointDecision, LucideIcon>;

/** Shared ask clarification copy — wire `kickoff` reuses the same shell as `decision`. */
const ASK_CLARIFY_META = {
  icon: CircleHelp,
  activeCaption: "需要你拍板",
  cta: "提交",
  ctaIcon: Check,
  showFooterHint: false,
  resolved: {
    // 普通澄清确认后结论文是用户答复本身；套话「已按你的决定继续」与答复、CEO 续聊三重叠。
    continue: { label: "", tone: "success" },
    adjust: { label: "已按你的调整继续", tone: "success" },
    // stop = 用户点「取消」硬停收口，非失败；与 timeout/orphaned 同档 muted，时间线占存根。
    stop: { label: "已取消本回合", tone: "muted" },
    research_first: { label: "已取消本回合", tone: "muted" },
    timeout: { label: "未及时回应，已自行收尾", tone: "muted" },
    orphaned: {
      label: "已失效（回合已结束或服务已重启）",
      tone: "muted",
    },
  },
} as const satisfies AskIntentMeta;

export const ASK_INTENT_META = {
  /** Wire may still emit kickoff; UX = generic clarification (same as decision). */
  kickoff: ASK_CLARIFY_META,
  decision: ASK_CLARIFY_META,
  proposal_pick: {
    icon: Layers,
    activeCaption: "方案挑选 · 选一条推进",
    cta: "采用此方案",
    ctaIcon: Layers,
    showFooterHint: false,
    resolved: {
      continue: { label: "已选定方案", tone: "success" },
      adjust: { label: "已按你的调整继续", tone: "success" },
      stop: { label: "已取消本回合", tone: "muted" },
      research_first: { label: "已取消本回合", tone: "muted" },
      timeout: { label: "未及时回应，已自行收尾", tone: "muted" },
      orphaned: {
        label: "已失效（回合已结束或服务已重启）",
        tone: "muted",
      },
    },
  },
  risk_ack: {
    icon: ShieldAlert,
    activeCaption: "风险确认 · 勾选本轮处理项",
    cta: "确认并继续",
    ctaIcon: ShieldAlert,
    showFooterHint: false,
    resolved: {
      continue: { label: "已确认风险处理项", tone: "success" },
      adjust: { label: "已按你的调整继续", tone: "success" },
      stop: { label: "已取消本回合", tone: "muted" },
      research_first: { label: "已取消本回合", tone: "muted" },
      timeout: { label: "未及时回应，已自行收尾", tone: "muted" },
      orphaned: {
        label: "已失效（回合已结束或服务已重启）",
        tone: "muted",
      },
    },
  },
  organize_plan: {
    icon: FolderTree,
    activeCaption: "整理方案 · 确认要执行的项",
    cta: "确认并整理",
    ctaIcon: FolderTree,
    showFooterHint: false,
    resolved: {
      continue: { label: "已确认整理方案", tone: "success" },
      adjust: { label: "已按你的调整继续", tone: "success" },
      stop: { label: "已取消本回合", tone: "muted" },
      research_first: { label: "已取消本回合", tone: "muted" },
      timeout: { label: "未及时回应，已自行收尾", tone: "muted" },
      orphaned: {
        label: "已失效（回合已结束或服务已重启）",
        tone: "muted",
      },
    },
  },
  daily_review: {
    icon: BookOpenCheck,
    activeCaption: "复盘提案 · 确认要落盘的项",
    cta: "确认落盘",
    ctaIcon: BookOpenCheck,
    showFooterHint: false,
    resolved: {
      continue: { label: "已确认复盘提案", tone: "success" },
      adjust: { label: "已按你的调整继续", tone: "success" },
      stop: { label: "已取消本回合", tone: "muted" },
      research_first: { label: "已取消本回合", tone: "muted" },
      timeout: { label: "未及时回应，已自行收尾", tone: "muted" },
      orphaned: {
        label: "已失效（回合已结束或服务已重启）",
        tone: "muted",
      },
    },
  },
} as const satisfies Record<CheckpointIntent, AskIntentMeta>;

export type AskResolvedOutcome = {
  label: string;
  tone: ResolvedToneKey;
  icon: LucideIcon;
};

export function askResolvedOutcome(
  intent: CheckpointIntent,
  decision: CheckpointDecision,
): AskResolvedOutcome {
  const resolved = ASK_INTENT_META[intent].resolved[decision];
  return {
    label: resolved.label,
    tone: resolved.tone,
    icon: ASK_RESOLVED_DECISION_ICON[decision],
  };
}

/** 线材没带可识别 decision 时的结论文——宁可短，不许猜成超时。 */
export const SETTLED_UNKNOWN_LABEL = "已经处理过了";

const SETTLED_UNKNOWN: AskResolvedOutcome = {
  label: SETTLED_UNKNOWN_LABEL,
  tone: "muted",
  icon: Check,
};

/**
 * ask 结算脸：只有线材 `timeout` 才用超时文案。缺字段 / 未识别取值不猜。
 * 取消（stop / 误用 research_first）与确认、超时同形占时间线存根。
 */
export function askResolvedDisplay(
  intent: CheckpointIntent,
  decision: CheckpointDecision | null | undefined,
): AskResolvedOutcome {
  if (decision && decision in ASK_RESOLVED_DECISION_ICON) {
    return askResolvedOutcome(intent, decision);
  }
  return SETTLED_UNKNOWN;
}

type TeamResolvedRow = { label: string; icon: LucideIcon };

/** Revision chrome + change-line templates. `{n}` / `{name}` / `{from}` / `{to}`. */
export type TeamRevisionMeta = {
  versionLabel: string;
  caption: string;
  noteLabel: string;
  noteExpand: string;
  noteCollapse: string;
  changesLead: string;
  unnamed: string;
  added: string;
  removed: string;
  renamed: string;
  roleChanged: string;
  writeChanged: string;
  planChanged: string;
  motionChanged: string;
  stanceChanged: string;
};

export type TeamPrimitiveMeta = {
  resumeLead: string;
  resumeCta: string;
  notePlaceholder: string;
  /** Adjust-state primary — submit `adjust`, never continue. */
  adjustCta: string;
  adjustPlaceholder: string;
  resolved: Record<CheckpointDecision, TeamResolvedRow>;
  /** continue + non-empty note overrides the continue label. */
  continueWithNote: TeamResolvedRow;
  revision: TeamRevisionMeta;
};

const TEAM_REVISION_CHROME = {
  versionLabel: "第 {n} 版",
  caption: "按你的意见修订",
  noteLabel: "你交回的意见",
  noteExpand: "展开全文",
  noteCollapse: "收起",
  changesLead: "相对上一版",
  renamed: "{from} → {to}",
  roleChanged: "{name}：角色/职责有变",
  writeChanged: "{name}：写盘能力有变",
  planChanged: "{name}：计划步骤有变",
  motionChanged: "辩题有变",
  stanceChanged: "{name}：立场有变",
} as const;

export const TEAM_PRIMITIVE_META = {
  delegate: {
    // 旧 payload 无 headline 时的兜底导语；有人数时前端会优先「预计 N 人开工」。
    resumeLead: "预计开工。等待你确认后才会上场，分工如下：",
    resumeCta: "授权并开工",
    notePlaceholder: "开工时注入全体队员",
    adjustCta: "交回修订",
    adjustPlaceholder: "填写意见，交给 CEO 修订开工方案",
    resolved: {
      continue: {
        icon: Check,
        label: "已授权开工 · 首波已放行",
      },
      adjust: {
        icon: Pencil,
        label: "已调整 · 已交回修订",
      },
      stop: { icon: OctagonX, label: "已取消 · 团队未启动" },
      // research_first 仅辩论开工卡合法；误落到 delegate 时按取消文案降级展示。
      research_first: {
        icon: OctagonX,
        label: "已取消 · 团队未启动",
      },
      timeout: { icon: Clock, label: "未及时回应，团队未启动" },
      orphaned: {
        icon: Ban,
        label: "已失效（回合已结束或服务已重启）",
      },
    },
    continueWithNote: {
      icon: Check,
      label: "已授权开工 · 嘱咐已注入队员",
    },
    revision: {
      ...TEAM_REVISION_CHROME,
      unnamed: "未命名岗",
      added: "新增 {name}",
      removed: "去掉 {name}",
    },
  },
  debate: {
    resumeLead: "预计开赛。等待你确认后才会开赛，辩题与立场如下：",
    resumeCta: "授权开赛",
    notePlaceholder: "开赛时注入各方",
    adjustCta: "交回修订",
    adjustPlaceholder: "填写意见，交给 CEO 修订开赛方案",
    resolved: {
      continue: {
        icon: Check,
        label: "已授权开赛 · 辩论已放行",
      },
      adjust: {
        icon: Pencil,
        label: "已调整 · 已交回修订",
      },
      stop: { icon: OctagonX, label: "已取消 · 辩论未开赛" },
      research_first: {
        icon: Scale,
        label: "已选先调研 · 辩论未开赛",
      },
      timeout: { icon: Clock, label: "未及时回应，辩论未开赛" },
      orphaned: {
        icon: Ban,
        label: "已失效（回合已结束或服务已重启）",
      },
    },
    continueWithNote: {
      icon: Check,
      label: "已授权开赛 · 嘱咐已注入",
    },
    revision: {
      ...TEAM_REVISION_CHROME,
      unnamed: "未命名辩手",
      added: "新增辩手 {name}",
      removed: "去掉辩手 {name}",
    },
  },
} as const satisfies Record<KickoffPrimitive, TeamPrimitiveMeta>;

export type TeamResolvedOutcome = TeamResolvedRow;

/**
 * Kickoff card lead: prefer backend ``headline``; else local headcount fallback
 * (旧 payload 无字段不崩，仍可见人数).
 */
export function teamPreviewLead(args: {
  primitive: KickoffPrimitive;
  headline?: string | null;
  workerCount: number;
  sideCount: number;
}): string {
  const fromWire = (args.headline ?? "").trim();
  if (fromWire) return fromWire;
  if (args.primitive === "debate") {
    const n = args.sideCount;
    return n > 0 ? `预计 ${n} 方开赛` : TEAM_PRIMITIVE_META.debate.resumeLead;
  }
  const n = args.workerCount;
  return n > 0 ? `预计 ${n} 人开工` : TEAM_PRIMITIVE_META.delegate.resumeLead;
}

/** 已授权后的人数后缀：不再用「预计开工 / 开赛」（那是拍板前的话）. */
export function teamPreviewSettledLead(args: {
  primitive: KickoffPrimitive;
  headline?: string | null;
  workerCount: number;
  sideCount: number;
}): string {
  const fromWire = (args.headline ?? "").trim();
  if (fromWire) return fromWire;
  if (args.primitive === "debate") {
    const n = args.sideCount;
    return n > 0 ? `${n} 方` : "";
  }
  const n = args.workerCount;
  return n > 0 ? `${n} 人` : "";
}

export function teamResolvedOutcome(
  primitive: KickoffPrimitive,
  decision: CheckpointDecision,
  hasNote: boolean,
): TeamResolvedOutcome {
  const table = TEAM_PRIMITIVE_META[primitive];
  if (decision === "continue" && hasNote) {
    return table.continueWithNote;
  }
  return table.resolved[decision] ?? table.resolved.continue;
}

/** 开工卡结算脸：缺 decision 不猜超时、不降级成已授权。 */
export function teamResolvedDisplay(
  primitive: KickoffPrimitive,
  decision: CheckpointDecision | null | undefined,
  hasNote: boolean,
): TeamResolvedOutcome {
  if (decision && decision in TEAM_PRIMITIVE_META[primitive].resolved) {
    return teamResolvedOutcome(primitive, decision, hasNote);
  }
  return { icon: Check, label: SETTLED_UNKNOWN_LABEL };
}

/**
 * Resolved 对账后缀：已排除 k 岗 / 已收紧写盘。缺省空 → 无后缀（同旧）。
 * 辩论开赛卡一般无修正字段；有则同样展示。
 */
/** revision < 2（含缺省）不标版本，避免首版噪音。 */
export function teamPreviewRevisionVersionLabel(
  primitive: KickoffPrimitive,
  revision: number | null | undefined,
): string | null {
  const n =
    typeof revision === "number" && Number.isFinite(revision)
      ? Math.floor(revision)
      : 1;
  if (n < 2) return null;
  return TEAM_PRIMITIVE_META[primitive].revision.versionLabel.replaceAll(
    "{n}",
    String(n),
  );
}

export function fillTeamRevisionTemplate(
  template: string,
  vars: { n?: number; name?: string; from?: string; to?: string },
): string {
  return template
    .replaceAll("{n}", vars.n != null ? String(vars.n) : "")
    .replaceAll("{name}", vars.name ?? "")
    .replaceAll("{from}", vars.from ?? "")
    .replaceAll("{to}", vars.to ?? "");
}

export function teamCorrectionSuffix(args: {
  excluded_run_ids?: readonly string[] | null;
  write_capability_overrides?: ReadonlyArray<{
    run_id: string;
    capability: string;
  }> | null;
}): string {
  const parts: string[] = [];
  const excluded = args.excluded_run_ids?.length ?? 0;
  if (excluded > 0) parts.push(`已排除 ${excluded} 岗`);
  const tightened =
    args.write_capability_overrides?.filter((o) => o.capability === "text_only")
      .length ?? 0;
  if (tightened > 0) parts.push("已收紧写盘");
  return parts.length > 0 ? ` · ${parts.join(" · ")}` : "";
}
