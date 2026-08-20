/**
 * Production-shared chrome for the kickoff 开工提案 card (V2 Brief + Choose).
 * Preview variants import from here so layout A/B does not permanently fork.
 */
import { Button, Textarea } from "@/components/ui";
import { interactiveCheckpointTone } from "@/components/ui/tone-presets";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  guideDesktopDownload,
  isDesktopFolderAction,
} from "@/lib/desktopDownload";
import { organizeConfirmDetail } from "@/lib/grantFolderHints";
import type { AskAssumption, AskOption, AskQuestion } from "@/types/events";
import { Check, FolderOpen, FolderTree, Loader2 } from "lucide-react";
import { ASK_NOTE_PLACEHOLDER, type AskTone } from "./AskUserFields";

/** Kickoff option selection uses primary so chosen cards read clearly vs idle. */
export const COMMENCE_TONE = interactiveCheckpointTone.primary;

export type AskAnswerState = {
  answers: Record<string, string[]>;
  note: string;
  setNote: (v: string) => void;
  toggleChoice: (q: AskQuestion, opt: string) => void;
  setText: (q: AskQuestion, value: string) => void;
  presetCount: number;
};

/**
 * Split a brief into a short lead + bullet lines.
 * First non-empty line = conclusion; remaining lines = points (strips leading •/-).
 */
export function splitBriefContext(context: string): {
  lead: string;
  points: string[];
} {
  const lines = context
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return { lead: "", points: [] };
  const [lead, ...rest] = lines;
  const points = rest.map((l) => l.replace(/^[-•*]\s*/, ""));
  return { lead: lead ?? "", points };
}

/** Compact plan as secondary chips (label · value). */
export function PlanChips({
  assumptions,
  className = "",
  quiet = false,
}: {
  assumptions: AskAssumption[];
  className?: string;
  /** Quieter surface for secondary placement (brief footer). */
  quiet?: boolean;
}) {
  if (assumptions.length === 0) return null;
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {assumptions.map((a) => (
        <span
          key={a.id}
          className={
            quiet
              ? "inline-flex max-w-full items-baseline gap-1 rounded-lg bg-muted/30 px-2 py-0.5 text-xs text-muted-foreground"
              : "inline-flex max-w-full items-baseline gap-1 rounded-lg border border-border/60 bg-muted/25 px-2 py-0.5 text-xs"
          }
        >
          <span className="shrink-0 text-muted-foreground/80">{a.label}</span>
          <span
            className={`min-w-0 truncate ${quiet ? "text-muted-foreground" : "text-foreground/80"}`}
          >
            {a.value}
          </span>
        </span>
      ))}
    </div>
  );
}

export function CommenceNote({
  answer,
  disabled,
  compact = false,
  placeholder = ASK_NOTE_PLACEHOLDER,
  tone = COMMENCE_TONE,
}: {
  answer: Pick<AskAnswerState, "note" | "setNote">;
  disabled: boolean;
  compact?: boolean;
  placeholder?: string;
  tone?: AskTone;
}) {
  return (
    <Textarea
      value={answer.note}
      onChange={(e) => answer.setNote(e.target.value)}
      disabled={disabled}
      rows={compact ? 1 : 2}
      placeholder={placeholder}
      className={`w-full border-border bg-card placeholder:text-muted-foreground/70 ${tone.focus}`}
    />
  );
}

export function OptionButton({
  label,
  detail,
  recommended,
  isDefault,
  active,
  disabled,
  onClick,
  layout = "row",
  size = "md",
  tone = COMMENCE_TONE,
  leadingIcon,
}: {
  label: string;
  detail?: string;
  recommended?: boolean;
  isDefault?: boolean;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  /** row = legacy (detail below); card = tall 2-line; compact = single-line kickoff. */
  layout?: "row" | "card" | "compact";
  size?: "md" | "lg";
  tone?: AskTone;
  leadingIcon?: React.ReactNode;
}) {
  const badges = (
    <>
      {recommended && (
        <span className="shrink-0 rounded-lg bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
          推荐
        </span>
      )}
      {isDefault && !recommended && (
        <span className="shrink-0 rounded-lg bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground/70">
          默认
        </span>
      )}
    </>
  );

  if (layout === "compact") {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        aria-pressed={active}
        title={detail || undefined}
        className={`flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors disabled:opacity-40 ${
          active ? tone.optActive : tone.optIdle
        }`}
      >
        <span
          className={`flex size-4 shrink-0 items-center justify-center rounded-full border ${
            active ? tone.markActive : "border-border bg-transparent"
          }`}
          aria-hidden
        >
          {active && <Check size={10} strokeWidth={3} />}
        </span>
        {leadingIcon}
        <span className="shrink-0 text-xs font-medium text-foreground">
          {label}
        </span>
        {detail ? (
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {detail}
          </span>
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        {badges}
      </button>
    );
  }

  if (layout === "card") {
    const pad = size === "lg" ? "px-3 py-2.5" : "px-3 py-2";
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        aria-pressed={active}
        className={`flex w-full items-start gap-2.5 rounded-xl border text-left transition-colors disabled:opacity-40 ${pad} ${
          active ? tone.optActive : tone.optIdle
        }`}
      >
        <span
          className={`mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border ${
            active ? tone.markActive : "border-border bg-transparent"
          }`}
          aria-hidden
        >
          {active && <Check size={10} strokeWidth={3} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            {leadingIcon}
            <span
              className={`font-medium text-foreground ${
                size === "lg" ? "text-sm" : "text-xs"
              }`}
            >
              {label}
            </span>
            {badges}
          </span>
          {detail && (
            <span
              className={`mt-0.5 block text-xs leading-snug ${
                active ? "text-muted-foreground" : "text-muted-foreground/80"
              }`}
            >
              {detail}
            </span>
          )}
        </span>
      </button>
    );
  }

  return (
    <div className="flex w-full flex-col">
      <Button
        variant="ghost"
        disabled={disabled}
        onClick={onClick}
        className={`h-auto w-full justify-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-left text-xs font-normal disabled:opacity-40 ${
          active ? tone.optActive : tone.optIdle
        }`}
        icon={leadingIcon}
      >
        <span className="whitespace-pre-wrap">{label}</span>
        {recommended && (
          <span className="ml-1.5 shrink-0 text-muted-foreground">推荐</span>
        )}
        {isDefault && !recommended && (
          <span className="ml-1.5 shrink-0 text-muted-foreground/70">默认</span>
        )}
      </Button>
      {detail && (
        <span className="mt-0.5 px-2.5 text-xs text-muted-foreground">
          {detail}
        </span>
      )}
    </div>
  );
}

export function ChoiceQuestion({
  question,
  index,
  numbered,
  answer,
  disabled,
  onToggle,
  onSetText,
  optionLayout = "row",
  emphasizePrompt = false,
  optionSize = "md",
  optionColumns = 1,
  tone = COMMENCE_TONE,
  conversationId,
  bindBusyLabel,
  onBindOption,
  onFolderUnavailable,
}: {
  question: AskQuestion;
  index: number;
  numbered: boolean;
  answer: string[];
  disabled: boolean;
  onToggle: (opt: string) => void;
  onSetText?: (v: string) => void;
  optionLayout?: "row" | "card" | "compact";
  emphasizePrompt?: boolean;
  optionSize?: "md" | "lg";
  /** Card grid columns on wide viewports (falls back to single on narrow). */
  optionColumns?: 1 | 2;
  tone?: AskTone;
  conversationId?: string | null;
  bindBusyLabel?: string | null;
  onBindOption?: (opt: AskOption) => void;
  /** 本机目录 action 不可履约（Web 会附带打开下载页）；禁止 onToggle 假确认。 */
  onFolderUnavailable?: (message: string) => void;
}) {
  const canLocalFs = hasLocalFiles() && !!window.fsApi;
  const canBindAction = !!conversationId && !!onBindOption && canLocalFs;
  const twoColumn = optionLayout === "card" && optionColumns === 2;
  const compact = optionLayout === "compact";
  return (
    <div className="min-w-0">
      <div className="flex items-start gap-2">
        {numbered && (
          <span
            className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-medium ${tone.badge}`}
          >
            {index}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p
            className={`whitespace-pre-wrap text-foreground ${
              emphasizePrompt
                ? "text-base font-semibold leading-snug"
                : "text-sm font-medium"
            }`}
          >
            {question.prompt}
          </p>
          {question.kind === "choice" && question.multiple && (
            <span className="mt-1 inline-block text-xs text-muted-foreground">
              可多选
            </span>
          )}
        </div>
      </div>
      <div
        className={`${emphasizePrompt ? "mt-3" : "mt-1.5"} ${
          numbered && !emphasizePrompt ? "pl-7" : ""
        }`}
      >
        {question.kind === "text" ? (
          <input
            type="text"
            value={answer[0] ?? ""}
            onChange={(e) => onSetText?.(e.target.value)}
            disabled={disabled}
            placeholder={question.default || undefined}
            className={`w-full rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none disabled:opacity-40 ${tone.focus}`}
          />
        ) : (
          <div className={compact ? "space-y-1" : "space-y-1.5"}>
            <div
              className={
                optionLayout === "card"
                  ? `grid ${optionSize === "lg" ? "gap-2" : "gap-1.5"} ${
                      twoColumn
                        ? "grid-cols-1 sm:grid-cols-2"
                        : "sm:grid-cols-1"
                    }`
                  : compact
                    ? "flex flex-col gap-1"
                    : "flex flex-col gap-1.5"
              }
            >
              {question.options.map((opt) => {
                const desktopFolder = isDesktopFolderAction(opt.action);
                const organizeGrant = opt.action === "grant_organize_folder";
                const canRunFolder =
                  desktopFolder &&
                  (opt.action === "open_local_project"
                    ? !!onBindOption && canLocalFs
                    : canBindAction);
                const bindBusy = bindBusyLabel === opt.label;
                return (
                  <OptionButton
                    key={opt.label}
                    label={opt.label}
                    detail={organizeConfirmDetail(opt)}
                    recommended={opt.recommended}
                    isDefault={
                      !!question.default && opt.label === question.default
                    }
                    active={
                      canRunFolder && (answer.includes(opt.label) || !!bindBusy)
                    }
                    disabled={disabled || (!!bindBusyLabel && !bindBusy)}
                    onClick={() => {
                      if (!desktopFolder) {
                        onToggle(opt.label);
                        return;
                      }
                      if (!hasLocalFiles()) {
                        onFolderUnavailable?.(guideDesktopDownload());
                        return;
                      }
                      if (canRunFolder) {
                        onBindOption?.(opt);
                        return;
                      }
                      onFolderUnavailable?.(
                        opt.action === "open_local_project"
                          ? "打开本机文件夹仅桌面端可用"
                          : "本机目录授权仅桌面端可用",
                      );
                    }}
                    layout={optionLayout}
                    size={optionSize}
                    tone={tone}
                    leadingIcon={
                      desktopFolder ? (
                        bindBusy ? (
                          <Loader2
                            size={14}
                            className="shrink-0 animate-spin text-muted-foreground"
                          />
                        ) : organizeGrant ? (
                          <FolderTree
                            size={14}
                            className="shrink-0 text-muted-foreground"
                          />
                        ) : (
                          <FolderOpen
                            size={14}
                            className="shrink-0 text-muted-foreground"
                          />
                        )
                      ) : undefined
                    }
                  />
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
