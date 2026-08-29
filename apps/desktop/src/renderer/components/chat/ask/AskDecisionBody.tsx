/**
 * 生产通用澄清卡 —— AskCardShell + 行式选项（{@link AskRowGroup}）。
 * 无开场仪式主 CTA。打开不预选 `default`；AI 倾向写在选项 label 原文。`default` 走行右灰字「默认」。
 * `questions.length ≥ 2`：体内一次一题，头右侧 {@link AskQuestionPager} 可点切换各题
 * （没写补充也能切）；非末题主 CTA「下一题」（只推进），末题才「提交」才 resume。
 * 不是问卷 Wizard。提交仍须每题有勾选或人话。
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
  isGrantFolderAction,
} from "@/lib/desktopDownload";
import {
  ATTACH_CONFIRM_CAPTION,
  ATTACH_CONFIRM_CTA,
  ORGANIZE_CONFIRM_CAPTION,
  ORGANIZE_CONFIRM_CTA,
  grantHintsFromAskOption,
  organizeConfirmDetail,
  pickOralGrantOption,
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
import type { CheckpointUserDecision } from "@/services/checkpoint";
import type { AskOption, AskQuestion } from "@/types/events";
import { ArrowRight, FolderOpen, FolderTree, Loader2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AskCardFooter, AskCardShell, AskSectionLabel } from "./AskCardShell";
import { CommenceNote } from "./AskCommenceParts";
import { type AskRow, AskRowGroup } from "./AskOptionRow";
import { AskQuestionPager, resolveAskPrimaryAction } from "./AskQuestionPager";
import {
  type AskUserContent,
  hasExplicitAskReply,
  questionHasExplicitReply,
  type useAskAnswer,
} from "./AskUserFields";
import { LocalPickerFailureCard } from "./LocalPickerFailureCard";

const META = ASK_INTENT_META.decision;

type PickerFailureState = {
  kind: LocalPickerFailureKind;
  message?: string;
};

export function AskDecisionBody({
  content,
  answer,
  busy,
  submitting,
  caption,
  onContinue,
  onStop,
  conversationId,
  onBindResolve,
}: {
  content: AskUserContent;
  answer: ReturnType<typeof useAskAnswer>;
  busy: boolean;
  submitting: CheckpointUserDecision | null;
  caption?: string;
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
  if (seenQuestionSig !== questionSig) {
    setSeenQuestionSig(questionSig);
    setStep(0);
    setVisited(new Set([0]));
  }
  const canLocalFs = hasLocalFiles() && !!window.fsApi;
  const canBindAction = !!conversationId && !!onBindResolve && canLocalFs;

  const hasOrganizeGrantOption = content.questions.some((q) =>
    q.options.some((o) => o.action === "grant_organize_folder"),
  );
  const hasAttachGrantOption = content.questions.some((q) =>
    q.options.some((o) => o.action === "grant_attach_folder"),
  );

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

  /** 当前选中落在须本机履约的 option 上时返回之；Continue 不得退化成口头「已授权」。
   * 打开不再因 default 预选而出现「允许整理」——点授权行履约；人话短同意仍可交。 */
  const findPendingFolderOption = (): {
    q: AskQuestion;
    opt: AskOption;
  } | null => {
    for (const q of content.questions) {
      if (q.kind === "text") continue;
      for (const label of answer.answers[q.id] ?? []) {
        const opt = q.options.find((o) => o.label === label);
        if (opt && isDesktopFolderAction(opt.action)) {
          return { q, opt };
        }
      }
    }
    return null;
  };

  /**
   * 本题人话短允许表口头同意 → 同题 pending grant_*（hints 取自该选项）。
   * 仅当该题 listed 未勾选；已勾选 grant 仍走 {@link findPendingFolderOption}。
   * 禁对长文意图分类；未命中返回 null，Continue 走原 compose。
   */
  const findOralOrganizeGrant = (): {
    q: AskQuestion;
    opt: AskOption;
  } | null => {
    for (const q of content.questions) {
      if (q.kind === "text") continue;
      if ((answer.answers[q.id] ?? []).length > 0) continue;
      const opt = pickOralGrantOption(q.options, answer.notes[q.id] ?? "");
      if (opt) return { q, opt };
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
   * 继续：普通选项 → 原 onContinue；选中 grant_organize / bind_* / open_local_project /
   * register_local_project → 一键履约（对齐点选项行）。未知已删 action 当普通选项。
   * grant_organize 无系统选文件夹；找不到则卡面失败。同 root 只读已挂仍须点允许走
   * organize 履约（禁止静默升写）。本题人话命中整理短允许表且 listed 未勾选 → 同真
   * grant（非纯文本冒充已授权）。register 履约后 resume 本对话；open 开新会话不 resume。
   */
  const handleContinue = () => {
    if (busy || bindBusyLabel) return;
    const pending = findPendingFolderOption() ?? findOralOrganizeGrant();
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
    setStep(index);
    setVisited((prev) => {
      if (prev.has(index)) return prev;
      const next = new Set(prev);
      next.add(index);
      return next;
    });
  };

  const grantPending = findPendingFolderOption()?.opt.action;
  const hasFolderGrant = hasOrganizeGrantOption || hasAttachGrantOption;
  const shellCaption =
    grantPending === "grant_attach_folder" ||
    (hasAttachGrantOption && !hasOrganizeGrantOption)
      ? ATTACH_CONFIRM_CAPTION
      : hasFolderGrant
        ? ORGANIZE_CONFIRM_CAPTION
        : (caption ?? META.activeCaption);
  const shellIcon = hasFolderGrant ? FolderTree : META.icon;
  const hasQuestions = content.questions.length > 0;
  /** 无题：message 当唯一题干进壳标题。有题：不画总标题，题干在体内。 */
  const shellTitle = hasQuestions ? undefined : content.question;
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
  const shellCta = advancing
    ? "下一题"
    : grantPending === "grant_attach_folder"
      ? ATTACH_CONFIRM_CTA
      : grantPending === "grant_organize_folder"
        ? ORGANIZE_CONFIRM_CTA
        : META.cta;
  const shellCtaIcon = advancing
    ? ArrowRight
    : grantPending === "grant_organize_folder" ||
        grantPending === "grant_attach_folder"
      ? FolderTree
      : META.ctaIcon;
  const currentQuestion = hasQuestions
    ? content.questions[paged ? safeStep : 0]
    : undefined;
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
      const organizeGrant = isGrantFolderAction(opt.action);
      const canRunFolder =
        desktopFolder &&
        (opt.action === "open_local_project" ? canLocalFs : canBindAction);
      const bindBusy = bindBusyLabel === opt.label;
      return {
        key: opt.label,
        label: opt.label,
        // 通用卡一行；整理授权只留结构化「将整理：…」（helper 不透传模型副标题）。
        detail: organizeConfirmDetail(opt),
        hint: q.default && opt.label === q.default ? "默认" : undefined,
        icon: desktopFolder ? (
          bindBusy ? (
            <Loader2 size={12} className="animate-spin" />
          ) : organizeGrant ? (
            <FolderTree size={12} />
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
            answer.toggleChoice(q, opt.label);
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
      icon={shellIcon}
      caption={shellCaption}
      title={shellTitle}
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
        {content.assumptions.length > 0 && (
          <div className="space-y-1">
            <AskSectionLabel>起步计划</AskSectionLabel>
            <dl className="divide-y divide-border/60 px-2">
              {content.assumptions.map((a) => (
                <div key={a.id} className="space-y-0.5 py-1.5">
                  <dt className="text-xs leading-snug text-muted-foreground">
                    {a.label}
                  </dt>
                  <dd className="min-w-0 whitespace-pre-wrap text-xs leading-snug text-foreground/90">
                    {a.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {visibleQuestions.map((q) => {
          const stem =
            content.questions.length === 1 && !q.prompt.trim()
              ? content.question
              : q.prompt;
          return (
            <div key={q.id} data-ask-question-id={q.id}>
              <p className="px-2 whitespace-pre-wrap text-sm font-semibold leading-snug text-foreground">
                {stem}
                {q.kind === "choice" && q.multiple && (
                  <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                    可多选
                  </span>
                )}
              </p>
              {q.kind === "text" ? (
                <input
                  type="text"
                  value={(answer.answers[q.id] ?? [])[0] ?? ""}
                  onChange={(e) => answer.setText(q, e.target.value)}
                  disabled={busy}
                  placeholder={q.default || "填写你的答案"}
                  className="mt-2 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 focus:border-foreground/25 focus:outline-none disabled:opacity-40"
                />
              ) : (
                <>
                  <AskRowGroup
                    className="mt-1"
                    rows={questionRows(q)}
                    multiple={q.multiple}
                  />
                  <div className="mt-2 px-2">
                    <CommenceNote
                      answer={answer}
                      questionId={q.id}
                      disabled={busy}
                      compact
                    />
                  </div>
                </>
              )}
            </div>
          );
        })}

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
