import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/** `below` (default) = helper text under the control; `label` = inline with the
 *  label, for choices you must read *before* picking (模型槽位). */
export type SettingFieldHintPlacement = "below" | "label";

/** `stack` (default) = label over a full-width control. `inline` = label left,
 *  control right from `md`; stacks below 768px. Use inline for short names,
 *  selects, passwords, email; keep stack for long tokens and URLs. */
export type SettingFieldLayout = "stack" | "inline";

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
  layout?: SettingFieldLayout;
  /** Validation / submit failure for this field; announced via `role="alert"`. */
  error?: ReactNode;
  /** Right-aligned action on the label line (status badge, generate). */
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
 * One labelled form field. `stack` is label over the control; `inline` is label
 * left / control right from `md`, stacking again under 768px. Hint and error
 * sit under the control — on `inline` they indent to the control column.
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
  layout = "stack",
  error,
  action,
  fullWidth = true,
  className,
  children,
}: SettingFieldProps) {
  const inline = layout === "inline";
  const labelClass = cn(
    "text-xs font-medium text-foreground max-md:text-sm",
    inline && "md:whitespace-nowrap",
  );
  const labelId = htmlFor ? `${htmlFor}-label` : undefined;
  const hintId = htmlFor ? `${htmlFor}-hint` : undefined;
  const inlineHint = hintPlacement === "label" ? hint : null;
  const belowHint = hintPlacement === "below" ? hint : null;

  const header = (
    <div
      className={cn(
        "flex items-baseline justify-between gap-2",
        inline && "md:w-24 md:shrink-0 md:pt-2",
      )}
    >
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
      {!inline && action && <span className="shrink-0">{action}</span>}
    </div>
  );
  const control = (
    <div
      className={cn(
        inline ? "min-w-0 flex-1" : "mt-1.5",
        fullWidth && "[&>*]:w-full",
      )}
    >
      {inline && action ? (
        <div className="flex w-full items-center gap-2">
          <div className={cn("min-w-0 flex-1", fullWidth && "[&>*]:w-full")}>
            {children}
          </div>
          <span className="shrink-0">{action}</span>
        </div>
      ) : (
        children
      )}
    </div>
  );
  const messages = (
    <>
      {belowHint && (
        <p
          id={hintId}
          className={cn(
            "mt-1 text-xs text-muted-foreground",
            inline && "md:ml-2 md:pl-24",
          )}
        >
          {belowHint}
        </p>
      )}
      {error && (
        <p
          className={cn(
            "mt-1 text-xs text-muted-foreground",
            inline && "md:ml-2 md:pl-24",
          )}
          role="alert"
        >
          {error}
        </p>
      )}
    </>
  );

  if (!inline) {
    return (
      <div className={className}>
        {header}
        {control}
        {messages}
      </div>
    );
  }

  return (
    <div className={className}>
      <div className="flex flex-col gap-1.5 md:flex-row md:items-start md:gap-2">
        {header}
        {control}
      </div>
      {messages}
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
