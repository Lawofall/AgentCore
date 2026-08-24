import { Button, Textarea } from "@/components/ui";
import type { interactiveCheckpointTone } from "@/components/ui/tone-presets";
import {
  type LocalPickerFailureKind,
  formatBindLocalFolderAnswer,
  isLocalPickerFailureKind,
  pickAndBindLocalFolder,
} from "@/lib/bindLocalFolder";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  guideDesktopDownload,
  isDesktopFolderAction,
} from "@/lib/desktopDownload";
import {
  grantHintsFromAskOption,
  organizeConfirmDetail,
} from "@/lib/grantFolderHints";
import {
  formatGrantOrganizeFolderAnswer,
  pickAndGrantOrganizeFolder,
} from "@/lib/grantOrganizeFolder";
import { pickAndOpenLocalFolder } from "@/lib/openLocalFolder";
import {
  formatRegisterLocalFolderAnswer,
  pickAndRegisterLocalFolder,
} from "@/lib/registerLocalFolder";
import { usePersistentDisclosure } from "@/stores/disclosure";
import type {
  AskAssumption,
  AskOption,
  AskQuestion,
  CheckpointIntent,
} from "@/types/events";
import { ChevronRight, FolderOpen, FolderTree, Loader2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { LocalPickerFailureCard } from "./LocalPickerFailureCard";

/**
 * Shared 结构化问答内核 — the choice/text question UI + answer-state + answer composition
 * reused by BOTH asking surfaces: the CEO's `ask_user` ({@link AskUserCard}) and a worker's
 * blocking `escalate` ({@link EscalationCard}). Extracted here because it is the drift-prone
 * core (listed options + persistent free-text note, multi-select toggle, 答复模型 α
 * composition); the two cards only differ in their framing + footer (ask_user: 提交/取消;
 * escalate: 提交/按假设继续), which each owns. 设计: docs/03-AI核心/Agent协作模式.md（向用户发问）.
 */

/** 常驻人话框 — 选项 + 一句补充，前端不再注入「其他…」。 */
export const ASK_NOTE_PLACEHOLDER = "选项都不对，或有补充，写在这里";

/**
 * 旧 Ask 按钮 `grant_readonly_folder` 停履约：枚举仍解析，点了只报诚实失败
 *（不调目录选择器、不授权）。现行只读挂载走 `external_mount_readonly`。
 */
export const GRANT_READONLY_FOLDER_RETIRED =
  "旧版只读授权已停用，不会打开目录选择、也不会授权。只读区外目录改由 AI 直接挂载。";

/** The minimal ask content the shared fields render. A {@link CheckpointDisplay}
 * (live/replay), a paused-turn frame, and a worker escalation all satisfy it. */
export interface AskUserContent {
  question: string;
  assumptions: AskAssumption[];
  questions: AskQuestion[];
}

export type AskTone =
  (typeof interactiveCheckpointTone)[keyof typeof interactiveCheckpointTone];

/**
 * The answer-state engine for a structured ask: per-question picks (choice → option(s),
 * text → typed value) plus one persistent free-text note. Seeds each question from its
 * `default` so a 想省事 user can one-click submit an opening as-is. `compose(intent)`
 * flattens it all into ONE readable answer (答复模型 α — the only reader is the CEO /
 * worker, an LLM). Shared so both cards manage answers identically; each card decides
 * what to do with `compose()` in its own footer.
 */
export function useAskAnswer(
  content: AskUserContent,
  opts?: { seedAllMultiple?: boolean },
) {
  const [answers, setAnswers] = useState<Record<string, string[]>>(() => {
    const init: Record<string, string[]> = {};
    for (const q of content.questions) {
      if (opts?.seedAllMultiple && q.multiple && q.options.length > 0) {
        init[q.id] = q.options.map((o) => o.label);
      } else {
        init[q.id] = q.default ? [q.default] : [];
      }
    }
    return init;
  });
  const [note, setNote] = useState("");

  const toggleChoice = (q: AskQuestion, opt: string) => {
    setAnswers((cur) => {
      const picked = cur[q.id] ?? [];
      if (q.multiple) {
        return {
          ...cur,
          [q.id]: picked.includes(opt)
            ? picked.filter((o) => o !== opt)
            : [...picked, opt],
        };
      }
      return { ...cur, [q.id]: picked.includes(opt) ? [] : [opt] };
    });
  };

  const setText = (q: AskQuestion, value: string) => {
    setAnswers((cur) => ({ ...cur, [q.id]: value ? [value] : [] }));
  };

  // How many decisions already carry a value — surfaced on the opening CTA so a
  // 想省事 user sees the card is ready to ship as-is.
  const presetCount = content.questions.filter(
    (q) => (answers[q.id] ?? []).length > 0,
  ).length;

  const compose = (_intent: CheckpointIntent) =>
    composeAnswer(content, answers, note);

  /** Compose with one question forced to `value` (bind_local_folder resolve path). */
  const composeWithAnswer = (
    _intent: CheckpointIntent,
    questionId: string,
    value: string,
  ) => composeAnswer(content, { ...answers, [questionId]: [value] }, note);

  return {
    answers,
    note,
    setNote,
    toggleChoice,
    setText,
    presetCount,
    compose,
    composeWithAnswer,
  };
}

/**
 * The structured pickers — optional 起步计划 (read-only) + askable questions + 风格 —
 * driven by a {@link useAskAnswer} instance. Renders nothing it has no content for, so a
 * bare one-question escalate shows just that question and the CEO opening shows the full
 * set. The headline, note textarea, and footer live in the consuming card.
 */
export function AskQuestionFields({
  content,
  answer,
  tone,
  disabled,
  disclosureKey,
  conversationId,
  onBindResolve,
}: {
  content: AskUserContent;
  answer: ReturnType<typeof useAskAnswer>;
  tone: AskTone;
  disabled: boolean;
  /** 检查点/升级 id：给了才把「起步计划」开合持久化。 */
  disclosureKey?: string | null;
  /** Desktop conversation id — enables bind_local_folder action options. */
  conversationId?: string | null;
  /** After a successful bind, resolve the checkpoint with the composed answer. */
  onBindResolve?: (composedAnswer: string) => void | Promise<void>;
}) {
  const navigate = useNavigate();
  const [bindBusyLabel, setBindBusyLabel] = useState<string | null>(null);
  const [bindError, setBindError] = useState<string | null>(null);
  const [pickerFailure, setPickerFailure] = useState<{
    kind: LocalPickerFailureKind;
    message?: string;
  } | null>(null);

  const clearPickerFeedback = () => {
    setBindError(null);
    setPickerFailure(null);
  };

  const applyPickerFailure = (reason: string, message?: string) => {
    if (reason === "cancelled") return;
    if (isLocalPickerFailureKind(reason)) {
      setPickerFailure({ kind: reason, message });
      return;
    }
    setBindError(message ?? "本机目录操作失败");
  };

  const handleBindOption = async (q: AskQuestion, opt: AskOption) => {
    if (disabled || bindBusyLabel) return;

    if (opt.action === "grant_readonly_folder") {
      clearPickerFeedback();
      setBindError(GRANT_READONLY_FOLDER_RETIRED);
      return;
    }

    if (opt.action === "open_local_project") {
      if (!hasLocalFiles() || !window.fsApi) return;
      setBindBusyLabel(opt.label);
      clearPickerFeedback();
      const result = await pickAndOpenLocalFolder(navigate, {
        notifyOnFailure: false,
      });
      if (!result.ok) {
        applyPickerFailure(
          result.reason,
          result.reason === "cancelled" ? undefined : result.message,
        );
        setBindBusyLabel(null);
        return;
      }
      setBindBusyLabel(null);
      return;
    }

    if (!conversationId || !onBindResolve) return;
    setBindBusyLabel(opt.label);
    clearPickerFeedback();
    if (opt.action === "register_local_project") {
      if (!hasLocalFiles() || !window.fsApi) {
        applyPickerFailure("unavailable");
        setBindBusyLabel(null);
        return;
      }
      const result = await pickAndRegisterLocalFolder({
        notifyOnFailure: false,
      });
      if (!result.ok) {
        applyPickerFailure(
          result.reason,
          result.reason === "cancelled" ? undefined : result.message,
        );
        setBindBusyLabel(null);
        return;
      }
      const value = formatRegisterLocalFolderAnswer(
        opt.label,
        result.folder.name,
      );
      try {
        await onBindResolve(answer.composeWithAnswer("decision", q.id, value));
      } catch {
        setBindBusyLabel(null);
      }
      return;
    }
    if (opt.action === "grant_organize_folder") {
      const hints = grantHintsFromAskOption(opt);
      const result = await pickAndGrantOrganizeFolder(conversationId, hints);
      if (!result.ok) {
        if (result.reason === "unavailable") {
          setBindError("整理授权仅桌面端可用");
        } else {
          setBindError(result.message);
        }
        setBindBusyLabel(null);
        return;
      }
      const value = formatGrantOrganizeFolderAnswer(
        opt.label,
        result.displayLabel ?? result.root.name,
        result.namespace,
      );
      try {
        await onBindResolve(answer.composeWithAnswer("decision", q.id, value));
      } catch {
        setBindBusyLabel(null);
      }
      return;
    }
    const result = await pickAndBindLocalFolder(conversationId);
    if (!result.ok) {
      applyPickerFailure(
        result.reason,
        result.reason === "cancelled" ? undefined : result.message,
      );
      setBindBusyLabel(null);
      return;
    }
    const value = formatBindLocalFolderAnswer(opt.label, result.root.name);
    try {
      await onBindResolve(answer.composeWithAnswer("decision", q.id, value));
    } catch {
      setBindBusyLabel(null);
    }
  };

  return (
    <div className="space-y-2.5">
      {/* 起步计划：可折叠的只读信息块（默认收起；summary 预览各项名）。 */}
      {content.assumptions.length > 0 && (
        <AssumptionsDisclosure
          assumptions={content.assumptions}
          disclosureKey={disclosureKey}
        />
      )}

      {content.questions.map((q, i) => (
        <QuestionField
          key={q.id}
          index={i + 1}
          numbered={content.questions.length > 1}
          question={q}
          answer={answer.answers[q.id] ?? []}
          disabled={disabled}
          tone={tone}
          conversationId={conversationId}
          bindBusyLabel={bindBusyLabel}
          onToggleChoice={(opt) => answer.toggleChoice(q, opt)}
          onSetText={(v) => answer.setText(q, v)}
          onBindOption={(opt) => void handleBindOption(q, opt)}
          onFolderUnavailable={(msg) => {
            setPickerFailure(null);
            setBindError(msg);
          }}
          onLocalFsUnavailable={() => applyPickerFailure("unavailable")}
        />
      ))}

      {pickerFailure && (
        <LocalPickerFailureCard
          kind={pickerFailure.kind}
          message={pickerFailure.message}
        />
      )}
      {bindError && (
        <p className="text-xs text-muted-foreground">{bindError}</p>
      )}
    </div>
  );
}

/** Controlled 起步计划 fold — replaces native `<details>` so open state can persist. */
function AssumptionsDisclosure({
  assumptions,
  disclosureKey,
  defaultOpen = false,
}: {
  assumptions: AskAssumption[];
  disclosureKey?: string | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = usePersistentDisclosure(
    disclosureKey ? `${disclosureKey}:assumptions` : null,
    defaultOpen,
  );
  return (
    <div className="rounded-lg bg-muted/20">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-left"
      >
        <ChevronRight
          size={13}
          className={`shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span className="shrink-0 text-xs font-medium text-muted-foreground">
          起步计划
        </span>
        {!open && (
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground/70">
            {assumptions.map((a) => a.label).join(" · ")}
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-0.5 px-2.5 pb-2 pl-6">
          {assumptions.map((a) => (
            <div key={a.id} className="flex gap-1.5 text-xs">
              <span className="w-14 shrink-0 text-muted-foreground">
                {a.label}
              </span>
              <span className="min-w-0 flex-1 whitespace-pre-wrap text-foreground">
                {a.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** A shared note textarea bound to a {@link useAskAnswer} instance, so a card's free-form
 * 补充 box stays consistent. `placeholder` differs per surface (opening / fork / escalate). */
export function AskNoteField({
  answer,
  tone,
  disabled,
  placeholder,
}: {
  answer: ReturnType<typeof useAskAnswer>;
  tone: AskTone;
  disabled: boolean;
  placeholder: string;
}) {
  return (
    <Textarea
      value={answer.note}
      onChange={(e) => answer.setNote(e.target.value)}
      disabled={disabled}
      rows={2}
      placeholder={placeholder || ASK_NOTE_PLACEHOLDER}
      className={`w-full border-border bg-card placeholder:text-muted-foreground/70 ${tone.focus}`}
    />
  );
}

/** One askable item: a choice (radio / checkbox) or free-form text fill. `numbered`
 * shows a leading step badge (only when there is more than one question, so a lone
 * mid-task fork stays clean). */
function QuestionField({
  index,
  numbered,
  question,
  answer,
  disabled,
  tone,
  conversationId,
  bindBusyLabel,
  onToggleChoice,
  onSetText,
  onBindOption,
  onFolderUnavailable,
  onLocalFsUnavailable,
}: {
  index: number;
  numbered: boolean;
  question: AskQuestion;
  answer: string[];
  disabled: boolean;
  tone: AskTone;
  conversationId?: string | null;
  bindBusyLabel?: string | null;
  onToggleChoice: (opt: string) => void;
  onSetText: (value: string) => void;
  onBindOption?: (opt: AskOption) => void;
  /** 本机目录 action 不可履约时展示文案（Web 会附带打开下载页）；禁止 toggleChoice。 */
  onFolderUnavailable?: (message: string) => void;
  /** Desktop 无 fs / 无会话绑定能力时的固定失败卡。 */
  onLocalFsUnavailable?: () => void;
}) {
  const canLocalFs = hasLocalFiles() && !!window.fsApi;
  const canBindAction = !!conversationId && !!onBindOption && canLocalFs;

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        {numbered && (
          <span
            className={`flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-medium ${tone.badge}`}
          >
            {index}
          </span>
        )}
        <p className="min-w-0 flex-1 whitespace-pre-wrap text-sm text-foreground">
          {question.prompt}
        </p>
        {question.kind === "choice" && question.multiple && (
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            可多选
          </span>
        )}
      </div>
      <div className={`mt-1.5 ${numbered ? "pl-7" : ""}`}>
        {question.kind === "text" ? (
          <input
            type="text"
            value={answer[0] ?? ""}
            onChange={(e) => onSetText(e.target.value)}
            disabled={disabled}
            placeholder={question.default || undefined}
            className={`w-full rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none disabled:opacity-40 ${tone.focus}`}
          />
        ) : (
          <div className="space-y-1.5">
            <div className="flex flex-col gap-1.5">
              {question.options.map((opt) => {
                const active = answer.includes(opt.label);
                const isDefault =
                  !!question.default && opt.label === question.default;
                const desktopFolder = isDesktopFolderAction(opt.action);
                const organizeGrant = opt.action === "grant_organize_folder";
                const canRunFolder =
                  desktopFolder &&
                  (opt.action === "open_local_project"
                    ? canLocalFs
                    : canBindAction);
                const bindBusy = bindBusyLabel === opt.label;
                const confirmDetail = organizeConfirmDetail(opt);
                return (
                  <div key={opt.label} className="flex w-full flex-col">
                    <Button
                      variant="ghost"
                      disabled={disabled || (!!bindBusyLabel && !bindBusy)}
                      onClick={() => {
                        if (!desktopFolder) {
                          onToggleChoice(opt.label);
                          return;
                        }
                        if (opt.action === "grant_readonly_folder") {
                          onFolderUnavailable?.(GRANT_READONLY_FOLDER_RETIRED);
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
                        onLocalFsUnavailable?.();
                      }}
                      className={`h-auto w-full justify-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-left text-xs font-normal disabled:opacity-40 ${
                        (
                          desktopFolder
                            ? canRunFolder && (active || bindBusy)
                            : active
                        )
                          ? tone.optActive
                          : tone.optIdle
                      }`}
                      icon={
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
                    >
                      <span className="whitespace-pre-wrap">{opt.label}</span>
                      {opt.recommended && (
                        <span className="ml-1 shrink-0 text-muted-foreground">
                          ·推荐
                        </span>
                      )}
                      {isDefault && (
                        <span className="ml-1 shrink-0 text-muted-foreground">
                          ·默认
                        </span>
                      )}
                    </Button>
                    {confirmDetail && (
                      <span className="mt-0.5 px-2.5 text-xs text-muted-foreground">
                        {confirmDetail}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Compose the user's picks + free note into ONE readable answer the CEO / worker
 * can act on (答复模型 α). Exported for unit tests (bind_local_folder answer composition). */
export function composeAnswer(
  content: AskUserContent,
  answers: Record<string, string[]>,
  note: string,
): string {
  const trimmed = note.trim();
  if (content.questions.length === 0) {
    return trimmed;
  }
  const lines: string[] = [];
  for (const q of content.questions) {
    const picked = (answers[q.id] ?? []).map((s) => s.trim()).filter(Boolean);
    if (picked.length) lines.push(`· ${q.prompt}：${picked.join("、")}`);
    // 无勾选 + 有人话：不叠「按你的默认」；空提交仍按 default 走省事路径。
    else if (q.default && !trimmed) lines.push(`· ${q.prompt}：（按你的默认）`);
  }
  if (trimmed) lines.push(`· 补充：${trimmed}`);
  if (lines.length === 0) return trimmed;
  return ["我的答复：", ...lines].join("\n");
}
