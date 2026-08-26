import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/** `below` (default) = helper text under the control; `label` = inline with the
 *  label, for choices you must read *before* picking (模型槽位). */
export type SettingFieldHintPlacement = "below" | "label";

export interface SettingFieldProps {
  label: ReactNode;
  /**
   * The control's `id`. Wires `<label for>` and, by convention, the aria ids
   * `${htmlFor}-label` / `${htmlFor}-hint` — pass those to custom controls that
   * take `aria-labelledby` / `aria-describedby` instead of being a native input.
   */
  htmlFor?: string;
  hint?: ReactNode;
  hintPlacement?: SettingFieldHintPlacement;
  /** Validation / submit failure for this field; announced via `role="alert"`. */
  error?: ReactNode;
  /** Right-aligned action on the label line (清除 / 恢复跟随 / 生成). */
  action?: ReactNode;
  /**
   * Stretch the control to the field width (default). This is the guard against
   * the recurring slip of dropping `w-full` on an `Input` and shipping a stray
   * narrow box. Turn off for controls that must keep their intrinsic size.
   */
  fullWidth?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * One labelled form field: label (+ inline hint / action) over the control, with
 * hint and error lines underneath.
 *
 * Owns the label typography, the label→control gap and the field width, so the
 * settings forms stop each re-deciding those and stop diverging on which of the
 * two label recipes to use.
 */
export function SettingField({
  label,
  htmlFor,
  hint,
  hintPlacement = "below",
  error,
  action,
  fullWidth = true,
  className,
  children,
}: SettingFieldProps) {
  const labelClass = "text-xs font-medium text-foreground max-md:text-sm";
  const labelId = htmlFor ? `${htmlFor}-label` : undefined;
  const hintId = htmlFor ? `${htmlFor}-hint` : undefined;
  const inlineHint = hintPlacement === "label" ? hint : null;
  const belowHint = hintPlacement === "below" ? hint : null;

  return (
    <div className={className}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          {htmlFor ? (
            <label id={labelId} htmlFor={htmlFor} className={labelClass}>
              {label}
            </label>
          ) : (
            <span id={labelId} className={labelClass}>
              {label}
            </span>
          )}
          {inlineHint && (
            <span id={hintId} className="text-xs text-muted-foreground">
              {inlineHint}
            </span>
          )}
        </span>
        {action && <span className="shrink-0">{action}</span>}
      </div>
      <div className={cn("mt-1.5", fullWidth && "[&>*]:w-full")}>
        {children}
      </div>
      {belowHint && (
        <p id={hintId} className="mt-1 text-xs text-muted-foreground">
          {belowHint}
        </p>
      )}
      {error && (
        <p className="mt-1 text-xs text-muted-foreground" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export type SettingsFormMessageTone = "error" | "success";

const formMessageToneClass: Record<SettingsFormMessageTone, string> = {
  error: "text-muted-foreground",
  success: "text-success",
};

export interface SettingsFormMessageProps {
  /** `error` is announced assertively; `success` politely. */
  tone?: SettingsFormMessageTone;
  className?: string;
  children?: ReactNode;
}

/**
 * The outcome line for a whole form or action — "保存失败，请重试", "提交成功" —
 * as opposed to {@link SettingField}'s per-field `error`.
 *
 * Renders nothing when there is no message, so callers can pass state straight
 * through. Every settings form had its own bare `<p className="text-destructive">`
 * for this and only one of them set an aria role, so screen readers stayed silent
 * on most failures.
 */
export function SettingsFormMessage({
  tone = "error",
  className,
  children,
}: SettingsFormMessageProps) {
  if (!children) return null;
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={cn("text-xs", formMessageToneClass[tone], className)}
    >
      {children}
    </p>
  );
}
