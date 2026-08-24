/**
 * Shared Tailwind class presets for semantic tones (color-tokens.mdc).
 * Full literal strings so Tailwind v4 keeps them in the build.
 *
 * 极简中性配色（color-tokens 三层）：A 用户面 = 需要你 primary / 可恢复中断
 * {@link noticeChipNeutral} / 危险 destructive；B 执行态 failed = 红点；
 * C 分类可用 warning，StatusTone 不恢复 warning 槽。
 */

/** Brand shells for decision & checkpoint cards. */
export type BrandTone = "primary";

/** Execution / outcome chips and resolved records. */
export type StatusTone = "primary" | "success" | "destructive" | "muted";

export type DecisionShellTone = "primary" | "neutral";

/** Subtle card border + tinted background. */
export const surfaceSubtle: Record<BrandTone, string> = {
  primary: "border-primary/30 bg-primary/5",
};

/** CTA footer bar on decision cards. */
export const decisionCtaBar: Record<BrandTone, string> = {
  primary: "border-primary/15 bg-primary/10",
};

export const decisionShell: Record<DecisionShellTone, string> = {
  primary: surfaceSubtle.primary,
  neutral: "border-border bg-muted/40",
};

export const decisionCtaBarAll: Record<DecisionShellTone, string> = {
  primary: decisionCtaBar.primary,
  neutral: "border-border bg-muted/30",
};

export const decisionAccentText: Record<DecisionShellTone, string> = {
  primary: "text-primary",
  neutral: "text-muted-foreground",
};

/** Icon / label accent (no background). */
export const statusAccentText: Record<StatusTone, string> = {
  primary: "text-primary",
  success: "text-success",
  destructive: "text-destructive",
  muted: "text-muted-foreground",
};

/** Borderless soft fill pill body (pair with rounded-full + padding in JSX). */
export const statusPillSoft: Record<StatusTone, string> = {
  primary: "bg-primary/10 text-primary",
  success: "bg-success/10 text-success",
  destructive: "bg-destructive/10 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

/** Inline rounded-full status pill (full class string). */
export const statusPillInline: Record<StatusTone, string> = {
  primary: `rounded-full px-1.5 py-0.5 text-xs font-medium ${statusPillSoft.primary}`,
  success: `rounded-full px-1.5 py-0.5 text-xs font-medium ${statusPillSoft.success}`,
  destructive: `rounded-full px-1.5 py-0.5 text-xs ${statusPillSoft.destructive}`,
  muted: `rounded-full px-1.5 py-0.5 text-xs ${statusPillSoft.muted}`,
};

/** Confidence classification (debate brief) — not run status. */
export const confidencePill: Record<"high" | "medium" | "low", string> = {
  high: statusPillSoft.success,
  medium: statusPillSoft.muted,
  low: statusPillSoft.muted,
};

export const confidenceLabel: Record<"high" | "medium" | "low", string> = {
  high: "高",
  medium: "中",
  low: "低",
};

/** Primary text link (expand / drill-down affordance). */
export const textLinkPrimary =
  "inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline";

/** Muted meta count / stop-reason chip in card headers. */
export const countPillMuted =
  "shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground";

/** Round index label in debate narrative. */
export const roundLabelPill =
  "inline-block rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground";

/**
 * Debate round signal dot —— verdict-derived (clash → converge progression), NOT run
 * status (color-tokens.mdc: this is a debate-domain classification). Shared by the
 * narrative timeline rail and the convergence band so a round reads the same in both.
 *  - inflight  当前在飞那轮（尚未裁判）→ 品牌蓝脉动
 *  - converged 收敛终点 → 成功绿
 *  - clash     有真交锋 → 品牌蓝实心
 *  - quiet     各说各话 / 无交锋 → 中性灰
 */
export type DebateSignal = "inflight" | "converged" | "clash" | "quiet";
export const debateSignalDot: Record<DebateSignal, string> = {
  inflight: "bg-primary animate-pulse",
  converged: "bg-success",
  clash: "bg-primary",
  quiet: "bg-muted-foreground/30",
};
export const debateSignalText: Record<DebateSignal, string> = {
  inflight: "text-primary",
  converged: "text-success",
  clash: "text-primary",
  quiet: "text-muted-foreground",
};
/** 「一句人话」轮状态 pill 配色（与 {@link debateSignalDot} 同信号家族）：收敛绿 / 交锋蓝 / 平淡灰。 */
export const debateSignalPill: Record<DebateSignal, string> = {
  inflight: statusPillInline.primary,
  converged: statusPillInline.success,
  clash: statusPillInline.primary,
  quiet: statusPillInline.muted,
};

/** Brand-tinted panel (brief card, highlighted sections). */
export const brandPanelPrimary = `space-y-3 rounded-lg border p-4 ${surfaceSubtle.primary}`;

/** Neutral inset panels. */
export const surfaceMutedPanel = "rounded-lg border border-border bg-muted/30";
export const surfaceMutedPanelLight =
  "rounded-lg border border-border bg-muted/20";

/**
 * Graph node badges. 极简配色（协作图）：蓝色 (primary) 只留给「运行中 / 待你拍板」这类
 * 需要你的信号；纯分类信息（立场 / 修订 / 子任务 / 深度）一律走中性灰，避免蓝色
 * 「该看这里」的语义被稀释。`graphBadgePrimary` 仅剩「待你拍板」一个消费点。
 */
export const graphBadgePrimary = `flex shrink-0 items-center gap-1 ${statusPillInline.primary}`;
export const graphBadgeMuted = `flex shrink-0 items-center gap-1 ${statusPillInline.muted}`;
export const graphBadgeMutedPlain = `shrink-0 ${statusPillInline.muted}`;

/** Run status dot (color-tokens.mdc state mapping). */
export const runStatusDot = {
  pending: "bg-muted-foreground/30",
  running: "bg-primary",
  completed: "bg-success",
  failed: "bg-destructive",
  cancelled: "bg-muted-foreground/30",
  skipped: "bg-muted-foreground/30",
} as const;

/** Status / count chip (Badge, inline pills). */
export const statusChip: Record<StatusTone, string> = {
  primary: "border-primary/30 bg-primary/10 text-primary",
  success: "border-success/30 bg-success/10 text-success",
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
  muted: "border-border bg-muted text-muted-foreground",
};

/**
 * Neutral send-notice chrome: gray wash, readable body (not muted-foreground).
 * Distinct from {@link statusChip.muted} pills, which gray the text too.
 */
export const noticeChipNeutral = "border-border bg-muted/40 text-foreground";

/**
 * Interactive ask_user checkpoint shell.
 * - `primary`：全蓝（审批 / 队员升级等）。
 * - `neutral`：灰壳灰选项（AskUserCard 途中拍板；kickoff V2 选项用 primary 选中态）——行动信号在 Footer 主 CTA。
 */
export const interactiveCheckpointTone = {
  primary: {
    wrap: surfaceSubtle.primary,
    accent: "text-primary",
    badge: "bg-primary/10 text-primary",
    optActive: "border-primary bg-primary/10 text-foreground",
    optIdle:
      "border-border bg-card text-muted-foreground hover:border-primary/40 hover:bg-accent hover:text-foreground",
    markActive: "border-primary bg-primary text-primary-foreground",
    dot: "bg-primary-foreground",
    focus: "focus:border-primary/60",
    ctaBar: decisionCtaBar.primary,
    cta: "bg-primary text-primary-foreground hover:bg-primary/90",
  },
  neutral: {
    wrap: decisionShell.neutral,
    accent: "text-muted-foreground",
    badge: "bg-muted text-muted-foreground",
    optActive: "border-foreground/25 bg-muted text-foreground",
    optIdle:
      "border-border bg-card text-muted-foreground hover:border-foreground/20 hover:bg-accent hover:text-foreground",
    markActive: "border-foreground/50 bg-foreground text-background",
    dot: "bg-background",
    focus: "focus:border-foreground/25",
    ctaBar: decisionCtaBarAll.neutral,
    cta: "bg-primary text-primary-foreground hover:bg-primary/90",
  },
} as const;

/** Settled ask_user record shells (timeline metadata light cards). */
export const resolvedCheckpointTone = {
  success: {
    // Ghost row — no thick border/card fill; continue vs stop still differs by icon + label.
    wrap: "",
    badge: "bg-muted text-muted-foreground",
    label: "text-muted-foreground",
  },
  destructive: {
    // Weak warning chrome only; still not a white DecisionCard box.
    wrap: "border border-destructive/25 bg-destructive/5 px-3",
    badge: "bg-destructive/10 text-destructive",
    label: "text-destructive",
  },
  muted: {
    wrap: "",
    badge: "bg-muted text-muted-foreground",
    label: "text-muted-foreground",
  },
} as const;

/** Handoff / status card chrome keyed by execution state token. */
export function statusCardChrome(
  tone: "muted" | "primary" | "success" | "destructive",
): { accent: string; border: string; surface: string } {
  switch (tone) {
    case "primary":
      return {
        accent: "text-primary",
        border: "border-primary/30",
        surface: "bg-primary/10",
      };
    case "success":
      return {
        accent: "text-success",
        border: "border-border",
        surface: "bg-card/60",
      };
    case "destructive":
      return {
        accent: "text-destructive",
        border: "border-destructive/30",
        surface: "bg-destructive/10",
      };
    default:
      return {
        accent: "text-muted-foreground",
        border: "border-border",
        surface: "bg-card/60",
      };
  }
}
