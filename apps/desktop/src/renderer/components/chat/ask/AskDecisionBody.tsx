/**
 * 生产通用澄清卡 —— AskCardShell + 行式选项（{@link AskRowGroup}）。
 * Wire `intent=kickoff` 与 `decision` 均挂此体；无开场仪式主 CTA。
 * 彩色「推荐 / 默认」徽章已删：`default` 由 {@link useAskAnswer} 预选，选中态即其表达。
 */
import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
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
import {
  ORGANIZE_CONFIRM_CAPTION,
  ORGANIZE_CONFIRM_CTA,
  grantHintsFromAskOption,
  isOrganizeOralConsent,
  organizeConfirmDetail,
} from "@/lib/grantFolderHints";
import {
  formatGrantOrganizeFolderAnswer,
  pickAndGrantOrganizeFolder,
} from "@/lib/grantOrganizeFolder";
import {
  formatGrantReadonlyFolderAnswer,
  pickAndGrantReadonlyFolder,
} from "@/lib/grantReadonlyFolder";
import { pickAndOpenLocalFolder } from "@/lib/openLocalFolder";
import {
  formatRegisterLocalFolderAnswer,
  pickAndRegisterLocalFolder,
} from "@/lib/registerLocalFolder";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import type { AskOption, AskQuestion } from "@/types/events";
import {
  ChevronRight,
  FolderOpen,
  FolderTree,
  Loader2,
  Pencil,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AskCardFooter, AskCardShell, AskSectionLabel } from "./AskCardShell";
import { CommenceNote } from "./AskCommenceParts";
import { type AskRow, AskRowGroup } from "./AskOptionRow";
import type { AskUserContent, useAskAnswer } from "./AskUserFields";
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
  const [noteOpen, setNoteOpen] = useState(false);

  const canLocalFs = hasLocalFiles() && !!window.fsApi;
  const canBindAction = !!conversationId && !!onBindResolve && canLocalFs;

  const hasOrganizeGrantOption = content.questions.some((q) =>
    q.options.some((o) => o.action === "grant_organize_folder"),
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

  /** 当前选中（含 default 预选）落在须本机履约的 option 上时返回之；Continue 不得退化成口头「已授权」。 */
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
   * 「其他…」短允许表口头同意 → 同题 pending `grant_organize_folder`（hints 取自该选项）。
   * 禁对长文意图分类；未命中返回 null，Continue 走原 compose。
   */
  const findOralOrganizeGrant = (): {
    q: AskQuestion;
    opt: AskOption;
  } | null => {
    for (const q of content.questions) {
      if (q.kind === "text") continue;
      if (!answer.otherOn[q.id]) continue;
      if (!isOrganizeOralConsent(answer.otherText[q.id] ?? "")) continue;
      const opt = q.options.find((o) => o.action === "grant_organize_folder");
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

    if (opt.action === "grant_readonly_folder") {
      const hints = grantHintsFromAskOption(opt);
      const result = await pickAndGrantReadonlyFolder(conversationId, hints);
      if (!result.ok) {
        // not_found / not_directory / ambiguous / error → 卡面明确失败（≠ cancelled 静默）
        if (result.reason === "unavailable") {
          setBindError("区外目录授权仅桌面端可用");
        } else {
          setBindError(result.message);
        }
        setBindBusyLabel(null);
        return;
      }
      const value = formatGrantReadonlyFolderAnswer(
        opt.label,
        result.displayLabel ?? result.root.name,
        result.namespace,
      );
      try {
        await onBindResolve(answer.composeWithAnswer("decision", q.id, value));
      } catch {
        // resume 失败：留在卡上
      } finally {
        // resume 未 throw 且卡未卸载时，也须清 busy，避免主 CTA 永久卡住
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
   * 继续：普通选项 → 原 onContinue；选中 grant_* / bind_* / open_local_project /
   * register_local_project → 一键履约（对齐点选项行）。grant 无系统选文件夹；
   * 找不到则卡面失败、不提交口头授权。同 root 只读已挂仍须点允许走 organize 履约
   *（禁止静默升写）。「其他…」命中整理短允许表 → 同真 grant（非纯文本冒充已授权）。
   * register 履约后 resume 本对话；open 开新会话不 resume。
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

  const organizePending =
    findPendingFolderOption()?.opt.action === "grant_organize_folder";
  const shellCaption = hasOrganizeGrantOption
    ? ORGANIZE_CONFIRM_CAPTION
    : (caption ?? META.activeCaption);
  const shellIcon = hasOrganizeGrantOption ? FolderTree : META.icon;
  const shellCta = organizePending ? ORGANIZE_CONFIRM_CTA : META.cta;
  const shellCtaIcon = organizePending ? FolderTree : META.ctaIcon;

  const questionRows = (q: AskQuestion): AskRow[] => {
    const picked = answer.answers[q.id] ?? [];
    const rows: AskRow[] = q.options.map((opt) => {
      const desktopFolder = isDesktopFolderAction(opt.action);
      const organizeGrant = opt.action === "grant_organize_folder";
      const canRunFolder =
        desktopFolder &&
        (opt.action === "open_local_project" ? canLocalFs : canBindAction);
      const bindBusy = bindBusyLabel === opt.label;
      return {
        key: opt.label,
        label: opt.label,
        // 通用卡一行；整理授权只留结构化「将整理：…」（helper 不透传模型副标题）。
        detail: organizeConfirmDetail(opt),
        hint: opt.recommended && q.default !== opt.label ? "推荐" : undefined,
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
    rows.push({
      key: `${q.id}:__other__`,
      label: "其他…",
      icon: <Pencil size={12} />,
      muted: !answer.otherOn[q.id],
      selected: !!answer.otherOn[q.id],
      disabled: busy || !!bindBusyLabel,
      onSelect: () => answer.toggleOther(q),
    });
    return rows;
  };

  return (
    <AskCardShell
      variant="decision"
      icon={shellIcon}
      caption={shellCaption}
      title={content.question}
      extra={<ManualHelpLink to={MANUAL_HELP.checkpoint} />}
      footer={
        <AskCardFooter
          cta={shellCta}
          ctaIcon={shellCtaIcon}
          busy={busy || !!bindBusyLabel}
          submitting={submitting}
          onContinue={handleContinue}
          onStop={onStop}
        />
      }
    >
      <div className="space-y-3">
        {content.assumptions.length > 0 && (
          <div className="space-y-1">
            <AskSectionLabel>起步计划</AskSectionLabel>
            <dl className="divide-y divide-border/60 px-2">
              {content.assumptions.map((a) => (
                <div key={a.id} className="flex gap-3 py-1.5">
                  <dt className="w-16 shrink-0 text-xs leading-snug text-muted-foreground">
                    {a.label}
                  </dt>
                  <dd className="min-w-0 flex-1 whitespace-pre-wrap text-xs leading-snug text-foreground/90">
                    {a.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {content.questions.map((q) => (
          <div key={q.id}>
            <p className="px-2 text-xs font-medium leading-snug text-foreground">
              {q.prompt}
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
                {answer.otherOn[q.id] && (
                  <input
                    type="text"
                    value={answer.otherText[q.id] ?? ""}
                    onChange={(e) => answer.setOtherValue(q, e.target.value)}
                    disabled={busy}
                    // biome-ignore lint/a11y/noAutofocus: 用户点开「其他」才渲染此框，聚焦刚展开的字段是预期 UX。
                    autoFocus
                    placeholder="填写你的答案"
                    className="mt-1.5 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 focus:border-foreground/25 focus:outline-none disabled:opacity-40"
                  />
                )}
              </>
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

        <div className="px-2">
          <button
            type="button"
            onClick={() => setNoteOpen((v) => !v)}
            aria-expanded={noteOpen}
            className="flex w-full items-center gap-1.5 text-left"
          >
            <ChevronRight
              size={13}
              className={`shrink-0 text-muted-foreground transition-transform ${
                noteOpen ? "rotate-90" : ""
              }`}
            />
            <span className="shrink-0 text-xs text-muted-foreground">
              补充说明
            </span>
            {!noteOpen && answer.note.trim() && (
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground/70">
                {answer.note.trim()}
              </span>
            )}
          </button>
          {noteOpen && (
            <div className="mt-1.5 pl-5">
              <CommenceNote answer={answer} disabled={busy} compact />
            </div>
          )}
        </div>
      </div>
    </AskCardShell>
  );
}
