/**
 * daily_review — 复盘提案清单：行式多选（与 organize_plan 同壳）。
 * 默认全选（seedAllMultiple）；取消勾选即跳过。detail = review_kind 中文 + 摘要。
 */
import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
import { ASK_INTENT_META } from "@/components/chat/decision";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import type { AskOption } from "@/types/events";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { AskCardFooter, AskCardShell } from "./AskCardShell";
import { CommenceNote } from "./AskCommenceParts";
import { AskRowGroup } from "./AskOptionRow";
import type { AskUserContent, useAskAnswer } from "./AskUserFields";

const META = ASK_INTENT_META.daily_review;

const REVIEW_KIND_LABEL: Record<
  NonNullable<AskOption["review_kind"]>,
  string
> = {
  preference: "偏好",
  profile: "画像",
  topic: "主题",
  rule: "规则",
  doc: "文档",
};

function summarizeKinds(options: AskOption[]): string {
  const counts: Record<string, number> = {};
  for (const o of options) {
    const kind = o.review_kind;
    const label = kind ? REVIEW_KIND_LABEL[kind] : "其他";
    counts[label] = (counts[label] ?? 0) + 1;
  }
  const parts = Object.entries(counts).map(([k, n]) => `${k} ${n}`);
  return parts.length ? parts.join("、") : `${options.length} 项复盘提案`;
}

function optionDetail(option: AskOption): string | undefined {
  const kind =
    option.review_kind != null
      ? REVIEW_KIND_LABEL[option.review_kind]
      : undefined;
  const summary = (option.body ?? option.detail ?? "").trim();
  if (kind && summary) return `${kind} · ${summary}`;
  if (kind) return kind;
  return summary || undefined;
}

export function DailyReviewBody({
  content,
  answer,
  busy,
  submitting,
  caption,
  onContinue,
  onStop,
}: {
  content: AskUserContent;
  answer: ReturnType<typeof useAskAnswer>;
  busy: boolean;
  submitting: CheckpointUserDecision | null;
  caption?: string;
  onContinue: () => void;
  onStop: () => void;
}) {
  const q = content.questions[0];
  const picked = q ? (answer.answers[q.id] ?? []) : [];
  const overview = q ? summarizeKinds(q.options) : "";
  const [noteOpen, setNoteOpen] = useState(false);

  const subtitle = overview ? `总览：${overview}` : undefined;

  return (
    <AskCardShell
      variant="daily_review"
      icon={META.icon}
      caption={caption ?? META.activeCaption}
      title={content.question}
      subtitle={subtitle}
      extra={<ManualHelpLink to={MANUAL_HELP.checkpoint} />}
      footer={
        <AskCardFooter
          cta={picked.length > 0 ? `${META.cta}（${picked.length}）` : META.cta}
          ctaIcon={META.ctaIcon}
          busy={busy}
          submitting={submitting}
          onContinue={onContinue}
          onStop={onStop}
          ctaDisabled={picked.length === 0}
          hint="确认后服务端直接写入记忆/规则/文档，无需再跑工具"
        />
      }
    >
      <div className="space-y-3">
        {q && (
          <div>
            {q.prompt && (
              <p className="px-2 text-xs font-medium leading-snug text-foreground">
                {q.prompt}
                <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                  取消勾选即跳过
                </span>
              </p>
            )}
            <AskRowGroup
              className={q.prompt ? "mt-1" : undefined}
              multiple
              rows={q.options.map((opt) => ({
                key: opt.label,
                label: opt.label,
                detail: optionDetail(opt),
                selected: picked.includes(opt.label),
                disabled: busy,
                onSelect: () => answer.toggleChoice(q, opt.label),
              }))}
            />
          </div>
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
