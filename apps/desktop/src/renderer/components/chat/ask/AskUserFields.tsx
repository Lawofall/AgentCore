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
  isGrantFolderAction,
} from "@/lib/desktopDownload";
import {
  grantHintsFromAskOption,
  organizeConfirmDetail,
} from "@/lib/grantFolderHints";
import {
  formatGrantAttachFolderAnswer,
  formatGrantOrganizeFolderAnswer,
  pickAndGrantAttachFolder,
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
 * core (listed options + per-question free-text note, multi-select toggle, 答复模型 α
 * composition); the two cards only differ in their framing + footer (ask_user: 提交/取消;
 * escalate: 提交/按假设继续), which each owns. 设计: docs/03-AI核心/Agent协作模式.md（向用户发问）.
 */

/** 常驻人话框 — choice 选项下本题一句，前端不再注入「其他…」。 */
export const ASK_NOTE_PLACEHOLDER = "没有合适的，写在这里";

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
 * text → typed value) plus per-question free-text notes (choice only; keyed by
 * `question.id`). Cards with no questions keep a card-level `note`. Does not seed
 * `default` — the generic card opens with nothing checked (认同推荐项须再点一下).
 * `seedAllMultiple` still selects every option on organize / daily-review walls.
 * `compose(intent)` flattens it all into ONE readable answer (答复模型 α — the only
 * reader is the CEO / worker, an LLM). Empty picks + no note still emit「按你的默认」
 * for protocol compatibility; the desktop card must not send that path.
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
        init[q.id] = [];
      }
    }
    return init;
  });
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const setQuestionNote = (id: string, value: string) => {
    setNotes((cur) => ({ ...cur, [id]: value }));
  };

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

  // How many decisions already carry a value (retired kickoff preview chrome).
  const presetCount = content.questions.filter(
    (q) => (answers[q.id] ?? []).length > 0,
  ).length;

  const compose = (_intent: CheckpointIntent) =>
    composeAnswer(content, answers, notes, note);

  /** Compose with one question forced to `value` (bind_local_folder resolve path). */
  const composeWithAnswer = (
    _intent: CheckpointIntent,
    questionId: string,
    value: string,
  ) =>
    composeAnswer(content, { ...answers, [questionId]: [value] }, notes, note);

  return {
    answers,
    note,
    notes,
    setNote,
    setQuestionNote,
    toggleChoice,
    setText,
    presetCount,
    compose,
    composeWithAnswer,
  };
}

/**
 * The structured pickers — optional 起步计划 (read-only) + askable questions —
 * driven by a {@link useAskAnswer} instance. Renders nothing it has no content for, so a
 * bare one-question escalate shows just that question and the CEO opening shows the full
 * set. Choice questions carry 本题人话; the headline and footer live in the consuming card.
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
    if (
      opt.action === "grant_organize_folder" ||
      opt.action === "grant_attach_folder"
    ) {
      const hints = grantHintsFromAskOption(opt);
      const result =
        opt.action === "grant_attach_folder"
          ? await pickAndGrantAttachFolder(conversationId, hints)
          : await pickAndGrantOrganizeFolder(conversationId, hints);
      if (!result.ok) {
        if (result.reason === "unavailable") {
          setBindError(
            opt.action === "grant_attach_folder"
              ? "附加可写授权仅桌面端可用"
              : "整理授权仅桌面端可用",
          );
        } else {
          setBindError(result.message);
        }
        setBindBusyLabel(null);
        return;
      }
      const value =
        opt.action === "grant_attach_folder"
          ? formatGrantAttachFolderAnswer(
              opt.label,
              result.displayLabel ?? result.root.name,
              result.namespace,
            )
          : formatGrantOrganizeFolderAnswer(
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
          note={answer.notes[q.id] ?? ""}
          onToggleChoice={(opt) => answer.toggleChoice(q, opt)}
          onSetText={(v) => answer.setText(q, v)}
          onSetNote={(v) => answer.setQuestionNote(q.id, v)}
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

/** A shared note textarea bound to a {@link useAskAnswer} instance.
 * Pass `questionId` for 本题人话；omit it for 无题卡整卡 note.
 * `placeholder` differs per surface (opening / fork / escalate). */
export function AskNoteField({
  answer,
  questionId,
  tone,
  disabled,
  placeholder,
}: {
  answer: ReturnType<typeof useAskAnswer>;
  questionId?: string;
  tone: AskTone;
  disabled: boolean;
  placeholder: string;
}) {
  const value =
    questionId != null ? (answer.notes[questionId] ?? "") : answer.note;
  return (
    <Textarea
      value={value}
      onChange={(e) =>
        questionId != null
          ? answer.setQuestionNote(questionId, e.target.value)
          : answer.setNote(e.target.value)
      }
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
  note,
  onToggleChoice,
  onSetText,
  onSetNote,
  onBindOption,
  onFolderUnavailable,
  onLocalFsUnavailable,
}: {
  index: number;
  numbered: boolean;
  question: AskQuestion;
  answer: string[];
  note: string;
  disabled: boolean;
  tone: AskTone;
  conversationId?: string | null;
  bindBusyLabel?: string | null;
  onToggleChoice: (opt: string) => void;
  onSetText: (value: string) => void;
  onSetNote: (value: string) => void;
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
                const organizeGrant = isGrantFolderAction(opt.action);
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
            <Textarea
              value={note}
              onChange={(e) => onSetNote(e.target.value)}
              disabled={disabled}
              rows={2}
              placeholder={ASK_NOTE_PLACEHOLDER}
              className={`w-full border-border bg-card placeholder:text-muted-foreground/70 ${tone.focus}`}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/** 本题是否已答：choice = 勾选或本题人话；text = 输入框有字（不叠第二格）。 */
export function questionHasExplicitReply(
  question: AskQuestion,
  answers: Record<string, string[]>,
  notes: Record<string, string>,
): boolean {
  if ((answers[question.id] ?? []).some((s) => s.trim().length > 0)) {
    return true;
  }
  if (question.kind === "text") return false;
  return (notes[question.id] ?? "").trim().length > 0;
}

/** Explicit user input: every question has a pick/text or（choice）本题人话.
 * Card `default` does not count (打开不预选). Empty question list uses
 * `cardNote`（无题澄清可交；纯人话升级看整卡 note）. 整卡有字不得放行未答的其他题. */
export function hasExplicitAskReply(
  content: AskUserContent,
  answers: Record<string, string[]>,
  notes: Record<string, string>,
  cardNote = "",
): boolean {
  if (content.questions.length === 0) return cardNote.trim().length > 0;
  return content.questions.every((q) =>
    questionHasExplicitReply(q, answers, notes),
  );
}

/** Flatten per-question notes (or 整卡 note when there are no questions).
 * Specialized-card resume `note` and 取消 still send free text, not compose. */
export function flattenAskNotes(
  content: AskUserContent,
  notes: Record<string, string>,
  cardNote = "",
): string {
  if (content.questions.length === 0) return cardNote.trim();
  return content.questions
    .map((q) => (notes[q.id] ?? "").trim())
    .filter(Boolean)
    .join("\n");
}

/** Compose the user's picks + per-question notes into ONE readable answer the
 * CEO / worker can act on (答复模型 α). Exported for unit tests. */
export function composeAnswer(
  content: AskUserContent,
  answers: Record<string, string[]>,
  notes: Record<string, string>,
  cardNote = "",
): string {
  const cardTrimmed = cardNote.trim();
  if (content.questions.length === 0) {
    return cardTrimmed;
  }
  const lines: string[] = [];
  for (const q of content.questions) {
    const picked = (answers[q.id] ?? []).map((s) => s.trim()).filter(Boolean);
    const qNote = (notes[q.id] ?? "").trim();
    if (picked.length) {
      const head = `· ${q.prompt}：${picked.join("、")}`;
      lines.push(qNote ? `${head} · 补充：${qNote}` : head);
    } else if (qNote) {
      lines.push(`· ${q.prompt}：${qNote}`);
    } else if (q.default) {
      lines.push(`· ${q.prompt}：（按你的默认）`);
    }
  }
  if (lines.length === 0) return cardTrimmed;
  return ["我的答复：", ...lines].join("\n");
}
