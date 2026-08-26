/**
 * 统一 ask 卡壳 —— ask intent（decision/kickoff 同壳 · proposal_pick / risk_ack /
 * organize_plan / daily_review）共用的三段结构，差异只剩「体」里的插槽。
 *
 * 相对旧 kickoff 开场仪式刻意砍掉的三处硬分区：头部不再铺 `bg-muted/10`、不再压 `border-b`（标题与
 * 首行之间靠留白分段），底栏不再 `backdrop-blur`。卡内不出现品牌色，唯一的彩色出口是底栏主 CTA。
 *
 * 排版契约：头/底 `px-4`，体 `px-2`——{@link AskRowGroup} 的行自带 `px-2`，两者相加后行内文字
 * 与标题左对齐，而行的 hover 灰底比文字宽出 8px（参考卡的观感）。体里**非行式**的块（小节标题、
 * 起步计划、输入框）需自带 `px-2` 才能对齐。
 */
import { Button } from "@/components/ui";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import { Loader2, type LucideIcon, OctagonX } from "lucide-react";
import type { ReactNode } from "react";

export function AskCardShell({
  icon: Icon,
  caption,
  title,
  subtitle,
  extra,
  footer,
  variant,
  children,
}: {
  icon: LucideIcon;
  /** intent 标识行（{@link ASK_INTENT_META} 的 activeCaption），与图标同排。 */
  caption: string;
  /** 可选总标题；空则不画标题节点（通用澄清卡有题时把题干放在体内）。 */
  title?: string;
  /** 可选副标题（organize/daily_review 的本地总览等）。 */
  subtitle?: string;
  /** 头部右上角插槽（通用澄清多题时挂编号跳转）。 */
  extra?: ReactNode;
  footer?: ReactNode;
  /** `data-ask-card` 取值，供预览与截图定位。 */
  variant: string;
  children: ReactNode;
}) {
  return (
    <div
      data-ask-card={variant}
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      {/* 图标只在 caption 行，标题因此与体内文字同起于 16px（头 px-4 = 体 px-2 + 行 px-2）。 */}
      <div className="shrink-0 px-4 pb-2 pt-3">
        <div className="flex items-center gap-1.5">
          <Icon size={13} className="shrink-0 text-muted-foreground" />
          <p className="min-w-0 flex-1 text-xs font-medium text-muted-foreground">
            {caption}
          </p>
          {extra}
        </div>
        {title ? (
          <p className="mt-1.5 whitespace-pre-wrap text-sm font-semibold leading-snug text-foreground">
            {title}
          </p>
        ) : null}
        {subtitle && (
          <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
            {subtitle}
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">{children}</div>

      {footer && (
        <div className="shrink-0 border-t border-border px-4 py-2.5">
          {footer}
        </div>
      )}
    </div>
  );
}

/** 小节标题（起步计划 / 风格基调 / 题干上方）。自带 `px-2` 对齐行式选项。 */
export function AskSectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="px-2 text-xs font-medium text-muted-foreground">{children}</p>
  );
}

/** 底栏：主 CTA + 安静的取消（wire 仍 decision=stop 硬停）+ 右侧提示。五种 intent 同一形。 */
export function AskCardFooter({
  cta,
  ctaIcon: CtaIcon,
  busy,
  submitting,
  onContinue,
  onStop,
  hint,
  ctaDisabled = false,
}: {
  cta: string;
  ctaIcon: LucideIcon;
  busy: boolean;
  submitting: CheckpointUserDecision | null;
  onContinue: () => void;
  /** 次要 CTA「取消」；调用方仍发 resume decision=stop（硬停收口）。 */
  onStop: () => void;
  hint?: string;
  /** 额外禁用主 CTA（如 proposal_pick 尚未选中任一项）。busy 时仍会禁用。 */
  ctaDisabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <Button
        size="md"
        variant="primary"
        className="bg-primary text-primary-foreground hover:bg-primary/90"
        disabled={busy || ctaDisabled}
        onClick={onContinue}
        icon={
          submitting === "continue" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <CtaIcon size={14} />
          )
        }
      >
        {cta}
      </Button>
      <Button
        size="md"
        variant="ghost"
        disabled={busy}
        onClick={onStop}
        className="text-muted-foreground hover:text-foreground"
        icon={
          submitting === "stop" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <OctagonX size={14} />
          )
        }
      >
        取消
      </Button>
      {hint && (
        <span className="min-w-0 flex-1 text-xs text-muted-foreground">
          {hint}
        </span>
      )}
    </div>
  );
}
