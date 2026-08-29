/**
 * 已退役的 kickoff 卡 — V2 Brief + Choose（Notion AI / product-brief 风）。
 * 生产 ask 已统一为 {@link AskDecisionBody}；本文件只剩预览路由的
 * `ask-commence-v2` 对照场景在挂，留作历史视觉参照，勿再接生产。
 */
import { Button } from "@/components/ui";
import {
  formatBindLocalFolderAnswer,
  pickAndBindLocalFolder,
} from "@/lib/bindLocalFolder";
import { grantHintsFromAskOption } from "@/lib/grantFolderHints";
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
import type { AskAssumption, AskOption, AskQuestion } from "@/types/events";
import { ChevronRight, Loader2, OctagonX, Rocket } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ChoiceQuestion,
  CommenceNote,
  PlanChips,
  splitBriefContext,
} from "./AskCommenceParts";
import type { AskUserContent, useAskAnswer } from "./AskUserFields";

export function AskCommenceKickoffBody({
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
  const { lead, points } = splitBriefContext(content.question);
  const [bindBusyLabel, setBindBusyLabel] = useState<string | null>(null);
  const [bindError, setBindError] = useState<string | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);

  const handleBindOption = async (q: AskQuestion, opt: AskOption) => {
    if (busy || bindBusyLabel) return;
    if (opt.action === "open_local_project") {
      setBindBusyLabel(opt.label);
      setBindError(null);
      const result = await pickAndOpenLocalFolder(navigate);
      if (!result.ok) {
        if (result.reason === "error") setBindError(result.message);
        setBindBusyLabel(null);
        return;
      }
      setBindBusyLabel(null);
      return;
    }
    if (!conversationId || !onBindResolve) return;
    setBindBusyLabel(opt.label);
    setBindError(null);

    if (opt.action === "register_local_project") {
      const result = await pickAndRegisterLocalFolder();
      if (!result.ok) {
        if (result.reason === "error") setBindError(result.message);
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
      if (result.reason === "error") setBindError(result.message);
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
    <div
      data-ask-commence-variant="v2"
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      {/* Brief — 目标复述 2 行；点开全文 */}
      <div className="shrink-0 space-y-2 border-b border-border bg-muted/10 px-4 py-3">
        <div className="flex items-center gap-1.5">
          <Rocket size={14} className="shrink-0 text-muted-foreground" />
          <p className="min-w-0 flex-1 text-xs font-medium text-muted-foreground">
            开工提案 · 确认即开做
          </p>
        </div>

        <button
          type="button"
          onClick={() => setBriefOpen((v) => !v)}
          aria-expanded={briefOpen}
          className="flex w-full items-start gap-1.5 text-left"
        >
          <div className="min-w-0 flex-1">
            <p
              className={`text-sm font-semibold leading-snug text-foreground ${
                briefOpen ? "whitespace-pre-wrap" : "line-clamp-2"
              }`}
            >
              {lead || content.question}
            </p>
            {briefOpen && points.length > 0 && (
              <BriefPointList points={points} className="mt-1.5" />
            )}
          </div>
          <ChevronRight
            size={14}
            className={`mt-0.5 shrink-0 text-muted-foreground transition-transform ${
              briefOpen ? "rotate-90" : ""
            }`}
          />
        </button>

        {content.assumptions.length > 0 && (
          <PlanChipsEntry
            assumptions={content.assumptions}
            open={planOpen}
            onToggle={() => setPlanOpen((v) => !v)}
          />
        )}
      </div>

      {/* Choose — 题干 + 紧凑单行选项常驻 */}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-2.5">
        {content.questions.map((q, i) => (
          <ChoiceQuestion
            key={q.id}
            question={q}
            index={i + 1}
            numbered={content.questions.length > 1}
            answer={answer.answers[q.id] ?? []}
            disabled={busy || !!bindBusyLabel}
            onToggle={(opt) => answer.toggleChoice(q, opt)}
            onSetText={(v) => answer.setText(q, v)}
            optionLayout="compact"
            conversationId={conversationId}
            bindBusyLabel={bindBusyLabel}
            onBindOption={(opt) => void handleBindOption(q, opt)}
            onFolderUnavailable={(msg) => setBindError(msg)}
            askAnswer={answer}
          />
        ))}
        {bindError && (
          <p className="text-xs text-muted-foreground">{bindError}</p>
        )}

        {content.questions.length === 0 && (
          <CommenceNote answer={answer} disabled={busy} compact />
        )}
      </div>

      {/* Footer — CTA + 预填提示同一行 */}
      <div className="shrink-0 border-t border-border bg-card/95 px-3 py-2.5 backdrop-blur-sm">
        <div className="flex flex-wrap items-center gap-2.5">
          <Button
            size="md"
            variant="primary"
            className="bg-primary text-primary-foreground hover:bg-primary/90"
            disabled={busy}
            onClick={onContinue}
            icon={
              submitting === "continue" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Rocket size={14} />
              )
            }
          >
            就这样开做
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
          <span className="min-w-0 flex-1 text-xs text-muted-foreground">
            {answer.presetCount > 0
              ? `已预填 ${answer.presetCount} 项，直接开做或按需调整`
              : "也可直接在下方对话框回复"}
          </span>
        </div>
      </div>
    </div>
  );
}

/** 起步计划：前 2 项 +「+N」一行入口，点开全显。 */
function PlanChipsEntry({
  assumptions,
  open,
  onToggle,
}: {
  assumptions: AskAssumption[];
  open: boolean;
  onToggle: () => void;
}) {
  const preview = assumptions.slice(0, 2);
  const rest = assumptions.length - preview.length;
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 text-left"
      >
        <ChevronRight
          size={13}
          className={`shrink-0 text-muted-foreground transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <span className="shrink-0 text-xs text-muted-foreground">起步计划</span>
        {!open && (
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground/80">
            {preview.map((a) => `${a.label} ${a.value}`).join(" · ")}
          </span>
        )}
        {!open && rest > 0 && (
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            +{rest}
          </span>
        )}
      </button>
      {open && (
        <PlanChips assumptions={assumptions} quiet className="mt-1.5 pl-5" />
      )}
    </div>
  );
}

function BriefPointList({
  points,
  className = "",
}: {
  points: string[];
  className?: string;
}) {
  return (
    <ul className={`space-y-1 ${className}`}>
      {points.map((p) => (
        <li
          key={p}
          className="flex gap-2 text-xs leading-snug text-foreground/80"
        >
          <span
            className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground/50"
            aria-hidden
          />
          <span>{p}</span>
        </li>
      ))}
    </ul>
  );
}
