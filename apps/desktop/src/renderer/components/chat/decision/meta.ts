/**
 * Shared decision-card meta for ask_user presentation.
 *
 * team_preview 开工卡已退役；本表只服务 ask_user。
 *
 * One table per family variant (intent / primitive) — replaces the former parallel
 * INTENT_CONFIG (CheckpointCard). Kind / wire
 * contracts stay untouched; this is display copy + icons only.
 */
import type { resolvedCheckpointTone } from "@/components/ui/tone-presets";
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
