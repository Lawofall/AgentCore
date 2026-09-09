/**
 * 生产通用澄清卡 —— AskCardShell + 行式选项（{@link AskRowGroup}）。
 * 无开场仪式主 CTA。打开不预选 `default`；AI 倾向写在选项 label 原文。`default` 走行右灰字「默认」。
 * 当前题干画在卡头（无题则 `message`）；可见面不画「需要你拍板」和图标。
 * `questions.length ≥ 2`：体内一次一题，头右侧 {@link AskQuestionPager} 可点切换各题
 * （没写补充也能切）；非末题主 CTA「下一题」（只推进），末题才「提交」才 resume。
 * 单选首次勾选约 200ms 后自动切下一题；回看改选停在本题；末题不自动交。
 * 不是问卷 Wizard。提交仍须每题有勾选或人话。
 * choice 人话接在选项组末行（铅笔、不编号），不是选项下另开带框输入。
 */
import { ASK_INTENT_META } from "@/components/chat/decision";
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
import { pickAndOpenLocalFolder } from "@/lib/openLocalFolder";
import {
  formatRegisterLocalFolderAnswer,
  pickAndRegisterLocalFolder,
} from "@/lib/registerLocalFolder";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import type { AskOption, AskQuestion } from "@/types/events";
import { ArrowRight, FolderOpen, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AskCardFooter, AskCardShell } from "./AskCardShell";
import { CommenceNote } from "./AskCommenceParts";
import { type AskRow, AskRowGroup } from "./AskOptionRow";
import {
  ASK_AUTO_ADVANCE_MS,
  AskQuestionPager,
  resolveAskPrimaryAction,
  shouldAutoAdvanceAskQuestion,
} from "./AskQuestionPager";
import {
  ASK_NOTE_PLACEHOLDER,
  type AskUserContent,
  hasExplicitAskReply,
  questionHasExplicitReply,
  questionPresentsAsText,
  type useAskAnswer,
} from "./AskUserFields";
import { LocalPickerFailureCard } from "./LocalPickerFailureCard";

const META = ASK_INTENT_META.decision;

function askStem(
  content: AskUserContent,
  question: AskQuestion | undefined,
): string {
  if (!question) return content.question;
  if (content.questions.length === 1 && !question.prompt.trim()) {
    return content.question;
  }
  return question.prompt;
}

function AskTextInput({
  value,
  onChange,
  disabled,
  placeholder,
}: {
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
  placeholder: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!ref.current?.disabled) ref.current?.focus();
  }, []);
  return (
    <input
      ref={ref}
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      placeholder={placeholder}
      className="mt-2 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 focus:border-foreground/25 focus:outline-none disabled:opacity-40"
    />
  );
}

type PickerFailureState = {
  kind: LocalPickerFailureKind;
  message?: string;
};

export function AskDecisionBody({
  content,
  answer,
  busy,
  submitting,
  onContinue,
  onStop,
  conversationId,
  onBindResolve,
}: {
  content: AskUserContent;
  answer: ReturnType<typeof useAskAnswer>;
  busy: boolean;
  submitting: CheckpointUserDecision | null;
  onContinue: () => void;
  onStop: () => void;
  conversationId?: string | null;
  onBindResolve?: (composedAnswer: string) => void | Promise<void>;
}) {
  const navigate = useNavigate();
  const [bindBusyLabel, setBindBusyLabel] = useState<string | null>(null);
  const [bindError, setBindError] = useState<string | null>(null);
  const [pickerFailure, setPickerFailure] = useState<PickerFailureState | null>(
    null,
  );
  const questionSig = content.questions.map((q) => q.id).join("\0");
  const [step, setStep] = useState(0);
  const [visited, setVisited] = useState<ReadonlySet<number>>(
    () => new Set([0]),
  );
  const [seenQuestionSig, setSeenQuestionSig] = useState(questionSig);
  const advanceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearAdvanceTimer = () => {
    if (advanceTimerRef.current != null) {
      clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = null;
    }
  };
  useEffect(
    () => () => {
      if (advanceTimerRef.current != null) {
        clearTimeout(advanceTimerRef.current);
        advanceTimerRef.current = null;
      }
    },
    [],
  );
  if (seenQuestionSig !== questionSig) {
    setSeenQuestionSig(questionSig);
    setStep(0);
    setVisited(new Set([0]));
    if (advanceTimerRef.current != null) {
      clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = null;
    }
  }
  const canLocalFs = hasLocalFiles() && !!window.fsApi;
  const canBindAction = !!conversationId && !!onBindResolve && canLocalFs;

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

  /** 当前选中落在须本机履约的 option 上时返回之；Continue 不得退化成口头「已绑定」。
   * 打开不再因 default 预选而出现本机履约 CTA——点目录行履约。 */

  const findPendingFolderOption = (): {
    q: AskQuestion;
    opt: AskOption;
  } | null => {
    for (const q of content.questions) {
      if (questionPresentsAsText(q)) continue;
      for (const label of answer.answers[q.id] ?? []) {
        const opt = q.options.find((o) => o.label === label);
        if (opt && isDesktopFolderAction(opt.action)) {
          return { q, opt };
        }
      }
    }
    return null;
  };

  const handleBindOption = async (q: AskQuestion, opt: AskOption) => {
    if (busy || bindBusyLabel) return;

    if (opt.action === "open_local_project") {
      if (!canLocalFs) return;
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
      // New conversation started — leave this pause as-is (do not rewrite folder_id).
      setBindBusyLabel(null);
      return;
    }

    if (!conversationId || !onBindResolve) return;
    setBindBusyLabel(opt.label);
    clearPickerFeedback();

    if (opt.action === "register_local_project") {
      if (!canLocalFs) {
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
        // resume 失败：留在卡上
      } finally {
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
      // resume 失败：留在卡上
    } finally {
      setBindBusyLabel(null);
    }
  };

  /**
   * 继续：普通选项 → 原 onContinue；选中 bind_* / open_local_project /
   * register_local_project → 一键履约（对齐点选项行）。未知 action 当普通选项。
   * register 履约后 resume 本对话；open 开新会话不 resume。
   */
  const handleContinue = () => {
    if (busy || bindBusyLabel) return;
    const pending = findPendingFolderOption();
    if (!pending) {
      onContinue();
      return;
    }
    const { q, opt } = pending;
    if (!hasLocalFiles()) {
      setBindError(guideDesktopDownload());
      setPickerFailure(null);
      return;
    }
    const canRunFolder =
      opt.action === "open_local_project" ? canLocalFs : canBindAction;
    if (!canRunFolder) {
      applyPickerFailure("unavailable");
      return;
    }
    void handleBindOption(q, opt);
  };

  const goToQuestion = (index: number) => {
    clearAdvanceTimer();
    setStep(index);
    setVisited((prev) => {
      if (prev.has(index)) return prev;
      const next = new Set(prev);
      next.add(index);
      return next;
    });
  };

  const scheduleAdvance = (index: number) => {
    clearAdvanceTimer();
    advanceTimerRef.current = setTimeout(() => {
      advanceTimerRef.current = null;
      goToQuestion(index);
    }, ASK_AUTO_ADVANCE_MS);
  };

  const hasQuestions = content.questions.length > 0;
  const paged = content.questions.length >= 2;
  const safeStep = paged ? Math.min(step, content.questions.length - 1) : 0;
  const visibleQuestions = paged
    ? content.questions.slice(safeStep, safeStep + 1)
    : content.questions;
  const primaryAction = resolveAskPrimaryAction(
    content.questions.length,
    safeStep,
    visited,
  );
  const advancing = primaryAction.type === "advance";
  const shellCta = advancing ? "下一题" : META.cta;
  const shellCtaIcon = advancing ? ArrowRight : undefined;
  const currentQuestion = hasQuestions
    ? content.questions[paged ? safeStep : 0]
    : undefined;
  const shellTitle = askStem(content, currentQuestion);
  const multiHint =
    currentQuestion?.kind === "choice" &&
    currentQuestion.multiple &&
    currentQuestion.options.length > 0;
  const currentHasInput =
    !hasQuestions ||
    (currentQuestion != null &&
      questionHasExplicitReply(currentQuestion, answer.answers, answer.notes));
  const allReady = hasExplicitAskReply(
    content,
    answer.answers,
    answer.notes,
    answer.note,
  );
  const ctaDisabled =
    hasQuestions &&
    (primaryAction.type === "submit" ? !allReady : !currentHasInput);

  const handlePrimary = () => {
    if (busy || bindBusyLabel || ctaDisabled) return;
    if (primaryAction.type === "advance" || primaryAction.type === "jump") {
      goToQuestion(primaryAction.index);
      return;
    }
    handleContinue();
  };

  const questionRows = (q: AskQuestion): AskRow[] => {
    const picked = answer.answers[q.id] ?? [];
    const rows: AskRow[] = q.options.map((opt) => {
      const desktopFolder = isDesktopFolderAction(opt.action);
      const canRunFolder =
        desktopFolder &&
        (opt.action === "open_local_project" ? canLocalFs : canBindAction);
      const bindBusy = bindBusyLabel === opt.label;
      return {
        key: opt.label,
        label: opt.label,
        hint: q.default && opt.label === q.default ? "默认" : undefined,
        icon: desktopFolder ? (
          bindBusy ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <FolderOpen size={12} />
          )
        ) : undefined,
        // 普通选项：选中态跟 answers；本机目录 action：仅可履约时显示（含绑定中 busy）。
        // 勿把 selected 绑到 canRunFolder——那会让非 folder 选项永远无选中反馈。
        selected: desktopFolder
          ? canRunFolder && (picked.includes(opt.label) || bindBusy)
          : picked.includes(opt.label),
        disabled: busy || (!!bindBusyLabel && !bindBusy),
        onSelect: () => {
          if (!desktopFolder) {
            const prev = answer.answers[q.id] ?? [];
            const already = prev.includes(opt.label);
            answer.toggleChoice(q, opt.label);
            if (
              shouldAutoAdvanceAskQuestion({
                paged,
                multiple: q.multiple,
                presentsAsText: questionPresentsAsText(q),
                selecting: !already,
                hadPriorPick: prev.length > 0,
                pendingAdvance: advanceTimerRef.current != null,
                primaryAction,
              }) &&
              (primaryAction.type === "advance" ||
                primaryAction.type === "jump")
            ) {
              scheduleAdvance(primaryAction.index);
            } else if (already) {
              clearAdvanceTimer();
            }
            return;
          }
          // Web / 无本地文件：禁止退化成 toggleChoice（假确认）。
          if (!hasLocalFiles()) {
            setBindError(guideDesktopDownload());
            setPickerFailure(null);
            return;
          }
          if (canRunFolder) {
            void handleBindOption(q, opt);
            return;
          }
          applyPickerFailure("unavailable");
        },
      };
    });
    return rows;
  };

  return (
    <AskCardShell
      variant="decision"
      title={shellTitle}
      titleAddon={
        multiHint ? (
          <span className="ml-1.5 text-xs font-normal text-muted-foreground">
            可多选
          </span>
        ) : undefined
      }
      extra={
        paged ? (
          <AskQuestionPager
            total={content.questions.length}
            index={safeStep}
            disabled={busy || !!bindBusyLabel}
            visited={visited}
            onChange={goToQuestion}
          />
        ) : undefined
      }
      footer={
        <AskCardFooter
          cta={shellCta}
          ctaIcon={shellCtaIcon}
          busy={busy || !!bindBusyLabel}
          submitting={submitting}
          onContinue={handlePrimary}
          onStop={onStop}
          ctaDisabled={ctaDisabled}
        />
      }
    >
      <div
        className="space-y-3"
        data-ask-question-step={paged ? safeStep : undefined}
      >
        {visibleQuestions.map((q) => (
          <div key={q.id} data-ask-question-id={q.id}>
            {questionPresentsAsText(q) ? (
              <div className="px-2">
                <AskTextInput
                  value={(answer.answers[q.id] ?? [])[0] ?? ""}
                  onChange={(next) => answer.setText(q, next)}
                  disabled={busy}
                  placeholder={q.default || "填写你的答案"}
                />
              </div>
            ) : (
              <AskRowGroup
                className="mt-1"
                rows={questionRows(q)}
                multiple={q.multiple}
                note={{
                  value: answer.notes[q.id] ?? "",
                  onChange: (next) => answer.setQuestionNote(q.id, next),
                  disabled: busy,
                  placeholder: ASK_NOTE_PLACEHOLDER,
                }}
              />
            )}
          </div>
        ))}

        {pickerFailure && (
          <div className="px-2">
            <LocalPickerFailureCard
              kind={pickerFailure.kind}
              message={pickerFailure.message}
            />
          </div>
        )}
        {bindError && (
          <p className="px-2 text-xs text-muted-foreground">{bindError}</p>
        )}

        {!hasQuestions && (
          <div className="px-2">
            <CommenceNote answer={answer} disabled={busy} compact />
          </div>
        )}
      </div>
    </AskCardShell>
  );
}
