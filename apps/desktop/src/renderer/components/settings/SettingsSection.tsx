import { cn } from "@/lib/utils";
import type { HTMLAttributes, ReactNode } from "react";

/**
 * Vertical rhythm for a settings subpage body: the gap below `PageHeader`
 * plus one uniform gap between sections. Wrap the sections of a subpage in this
 * instead of hand-tuning `mt-6 space-y-*` per page.
 */
export function SettingsStack({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mt-6 space-y-8", className)} {...props} />;
}

/** `sm` = sub-section inside a subpage (default); `base` = the subpage's one
 *  primary block (外观 · 主题, 快捷键 · 全局快捷键). */
export type SettingsSectionTitleSize = "sm" | "base";
export type SettingsSectionTone = "default" | "danger";

const titleSizeClass: Record<SettingsSectionTitleSize, string> = {
  sm: "text-sm font-semibold",
  base: "text-base font-medium",
};

const titleToneClass: Record<SettingsSectionTone, string> = {
  default: "text-foreground",
  danger: "text-destructive",
};

export interface SettingsSectionProps {
  title: ReactNode;
  /** One muted line under the heading — what this block controls. */
  description?: ReactNode;
  /** Top-right slot: a section-level action (新建 / 退出其他所有设备 / 刷新). */
  action?: ReactNode;
  titleSize?: SettingsSectionTitleSize;
  /** `danger` tints the heading for irreversible blocks (危险区域). */
  tone?: SettingsSectionTone;
  /** Hairline above the section — for trailing blocks that are visually split
   *  off from the page body (关于 · 软件更新 / 法律与合规, 反馈 · 历史反馈).
   *  Replaces hand-written `border-t border-border pt-6`. */
  divider?: boolean;
  /** Extra classes on the content wrapper (e.g. `space-y-2`, `mt-4`). */
  contentClassName?: string;
  className?: string;
  children?: ReactNode;
}

/**
 * One titled block of a settings subpage: heading (+ description, + top-right
 * action) over a content slot, with the heading→content gap owned here.
 *
 * Every subpage used to spell this out itself, which is how four different
 * heading recipes (`text-sm font-semibold` / `text-sm font-medium` /
 * `text-base font-medium` / a bare `<p>`) and hand-written `border-t` rules
 * ended up on adjacent pages. Content chrome stays the caller's choice — wrap
 * children in `<Card>` when the block needs a surface.
 */
export function SettingsSection({
  title,
  description,
  action,
  titleSize = "sm",
  tone = "default",
  divider = false,
  contentClassName,
  className,
  children,
}: SettingsSectionProps) {
  return (
    <section
      className={cn(divider && "border-t border-border pt-6", className)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className={cn(titleSizeClass[titleSize], titleToneClass[tone])}>
            {title}
          </h2>
          {description && (
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      {children && (
        <div className={cn("mt-3", contentClassName)}>{children}</div>
      )}
    </section>
  );
}
