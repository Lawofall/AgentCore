import { Markdown } from "@/components/chat/Markdown";
import { Badge, Button } from "@/components/ui";
import type { BadgeTone } from "@/components/ui/badge";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import { useSidePanelStore } from "@/stores/sidePanel";
import { ChevronDown, ChevronRight } from "lucide-react";
import type {
  DebateCrossExamExchangeView,
  DebateCrossExamView,
} from "../model";
import { ModeratorIdentity } from "./ModeratorIdentity";
import {
  DEBATE_SPLIT_GRID,
  type DebateArenaLayout,
  partitionSides,
} from "./debateLayoutPreference";
import { summarizeText } from "./parseSpeechArguments";

const ANSWER_PREVIEW_LEN = 48;
const QUESTION_PREVIEW_LEN = 72;

/** 质询小节：审计清单范式；split 时按方入列，答案为主、问题降权。 */
export function CrossExamSection({
  exchanges,
  messageId,
  sceneKey,
  layoutMode = "stack",
}: {
  exchanges: DebateCrossExamView[];
  messageId: string;
  sceneKey: string;
  layoutMode?: DebateArenaLayout;
}) {
  const useSplit = layoutMode === "split";

  return (
    <div className="space-y-3">
      <ModeratorCrossExamCue />
      {useSplit ? (
        <SplitCrossExamColumns
          exchanges={exchanges}
          messageId={messageId}
          sceneKey={sceneKey}
        />
      ) : (
        exchanges.map((cx) => (
          <CrossExamSideBlock
            key={cx.targetKey}
            cx={cx}
            messageId={messageId}
            sceneKey={sceneKey}
          />
        ))
      )}
    </div>
  );
}

/** 质询阶段报幕：居中环节标题 + 主持人身份壳，低于轮次大标题一级。 */
function ModeratorCrossExamCue() {
  return (
    <div className="mt-3 border-t border-border pt-3 text-center">
      <h4 className="text-base font-semibold text-foreground">质询</h4>
      <p className="mt-1 flex flex-wrap items-center justify-center gap-1.5 text-xs text-muted-foreground">
        <ModeratorIdentity gavelSize={13} className="text-xs" />
        <span>发出必答质询</span>
      </p>
    </div>
  );
}

function SplitCrossExamColumns({
  exchanges,
  messageId,
  sceneKey,
}: {
  exchanges: DebateCrossExamView[];
  messageId: string;
  sceneKey: string;
}) {
  const { pro, con, others } = partitionSides(
    exchanges,
    (cx) => cx.targetKey,
    (cx) => cx.stance,
  );

  return (
    <>
      <div className={DEBATE_SPLIT_GRID}>
        <div className="min-w-0">
          {pro && (
            <CrossExamSideBlock
              cx={pro}
              messageId={messageId}
              sceneKey={sceneKey}
            />
          )}
        </div>
        <div className="min-w-0">
          {con && (
            <CrossExamSideBlock
              cx={con}
              messageId={messageId}
              sceneKey={sceneKey}
            />
          )}
        </div>
      </div>
      {others.map((cx) => (
        <CrossExamSideBlock
          key={cx.targetKey}
          cx={cx}
          messageId={messageId}
          sceneKey={sceneKey}
        />
      ))}
    </>
  );
}

function CrossExamSideBlock({
  cx,
  messageId,
  sceneKey,
}: {
  cx: DebateCrossExamView;
  messageId: string;
  sceneKey: string;
}) {
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const run = cx.answerRun;
  const streaming = run?.status === "running";
  const answerFailed = run?.status === "failed";
  const total = cx.exchanges.length;

  const openRunDetail = () => {
    if (!run) return;
    showRunDetail(messageId, run.id, `${cx.targetName} · 质询作答`);
  };

  const meta = (
    <>
      <span className="font-medium" style={{ color: cx.targetColorVar }}>
        {cx.targetName}
      </span>
      <span className="text-muted-foreground">
        {total === 0 ? "暂无质询问答" : `· ${total} 条质询`}
      </span>
    </>
  );

  return (
    <div
      className="border-l-[3px] pl-3"
      style={{ borderLeftColor: cx.targetColorVar }}
    >
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        {run ? (
          // 对齐 SpeakerBlock：点名字行打开该方作答 run 的详情侧栏。
          <Button
            variant="ghost"
            onClick={openRunDetail}
            className="h-auto justify-start gap-2 rounded-none px-0 py-0 text-xs hover:bg-transparent"
          >
            {meta}
          </Button>
        ) : (
          <span className="flex items-center gap-2">{meta}</span>
        )}
      </div>
      {total > 0 ? (
        <ul className="space-y-1">
          {cx.exchanges.map((ex, i) => (
            <CrossExamQaRow
              key={`${cx.targetKey}:${i}`}
              exchange={ex}
              index={i}
              streaming={streaming && i === cx.exchanges.length - 1}
              answerFailed={answerFailed}
              sceneKey={`${sceneKey}:qa:${cx.targetKey}:${i}`}
            />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CrossExamQaRow({
  exchange,
  index,
  streaming,
  answerFailed,
  sceneKey,
}: {
  exchange: DebateCrossExamExchangeView;
  index: number;
  streaming: boolean;
  answerFailed: boolean;
  sceneKey: string;
}) {
  const hasAnswer = exchange.answer.trim().length > 0;
  // 整条 live 流式（含逐字作答）保持自动展开，收场后交回持久化折叠。
  const live = streaming;
  const [answerOpen, toggleAnswerOpen] = useStreamAwareDisclosure(
    `${sceneKey}:ans`,
    live,
  );
  const status = resolveQaStatus({
    hasAnswer,
    streaming,
    answerFailed,
  });
  const answerPreview = previewAnswer(exchange.answer, {
    streaming,
    hasAnswer,
  });
  const questionPreview = summarizeText(
    exchange.question.trim().replace(/\s+/g, " "),
    QUESTION_PREVIEW_LEN,
  );

  return (
    <li className="list-none rounded-lg bg-muted/15">
      <button
        type="button"
        onClick={toggleAnswerOpen}
        aria-expanded={answerOpen}
        className="flex w-full items-start gap-1.5 px-2 py-1.5 text-left hover:bg-muted/35"
      >
        {status ? <QaStatusBadge status={status} /> : null}
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 text-xs leading-snug text-muted-foreground">
            <span className="text-muted-foreground/80">Q{index + 1}. </span>
            {questionPreview}
          </span>
          {!answerOpen && answerPreview.text.length > 0 && (
            <span
              className={`mt-0.5 block truncate text-sm leading-snug ${
                answerPreview.placeholder
                  ? "text-muted-foreground"
                  : "text-foreground"
              }`}
            >
              {answerPreview.text}
            </span>
          )}
        </span>
        {answerOpen ? (
          <ChevronDown
            size={12}
            className="mt-0.5 shrink-0 text-muted-foreground"
          />
        ) : (
          <ChevronRight
            size={12}
            className="mt-0.5 shrink-0 text-muted-foreground"
          />
        )}
      </button>

      <div
        className="grid transition-[grid-template-rows,opacity] duration-200 ease-out"
        style={{
          gridTemplateRows: answerOpen ? "1fr" : "0fr",
          opacity: answerOpen ? 1 : 0,
        }}
      >
        <div className="overflow-hidden">
          <div className="px-2 pb-2 pt-0.5 text-sm text-foreground">
            {streaming && hasAnswer ? (
              <p className="whitespace-pre-wrap break-words">
                {exchange.answer}
                <span
                  className="ml-0.5 inline-block h-[1em] w-px animate-pulse bg-primary align-text-bottom"
                  aria-hidden
                />
              </p>
            ) : hasAnswer ? (
              <Markdown content={exchange.answer} evidence />
            ) : (
              <p className="text-xs text-muted-foreground">
                {status?.label === "待答"
                  ? "作答中…"
                  : (status?.label ?? "未作答")}
              </p>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

/** 客观状态徽章：仅「作答中 / 作答失败 / 未作答」；有答文时不贴褒贬标签。 */
type QaStatusKind = "待答" | "作答失败" | "未作答";

function resolveQaStatus({
  hasAnswer,
  streaming,
  answerFailed,
}: {
  hasAnswer: boolean;
  streaming: boolean;
  answerFailed: boolean;
}): { label: QaStatusKind; tone: BadgeTone; pulse?: boolean } | null {
  // live 流式该条尚未定谳，统一「待答」脉冲。
  if (streaming) {
    return { label: "待答", tone: "primary", pulse: true };
  }
  // 有作答：中性原文，不贴「正面回应 / 回避」褒贬徽章。
  if (hasAnswer) {
    return null;
  }
  if (answerFailed) {
    return { label: "作答失败", tone: "destructive" };
  }
  return { label: "未作答", tone: "muted" };
}

function QaStatusBadge({
  status,
}: {
  status: { label: QaStatusKind; tone: BadgeTone; pulse?: boolean };
}) {
  return (
    <Badge
      tone={status.tone}
      pill
      className={`mt-0.5 font-medium ${status.pulse ? "animate-pulse" : ""}`}
    >
      {status.label}
    </Badge>
  );
}

/**
 * 折叠预览：剥 markdown → 纯文本截断；疑似原始 JSON / 质询 blob 时回落占位，
 * 绝不把原始 blob 露到折叠行。
 */
function previewAnswer(
  text: string,
  { streaming, hasAnswer }: { streaming: boolean; hasAnswer: boolean },
): { text: string; placeholder: boolean } {
  if (!hasAnswer) {
    // 无作答：流式提示「作答中」；收场无作答则不占预览行（徽章已表「未作答/作答失败」）。
    return { text: streaming ? "作答中…" : "", placeholder: true };
  }
  const trimmed = text.trim();
  if (looksLikeRawBlob(trimmed)) {
    return {
      text: streaming ? "作答中…" : "点开查看",
      placeholder: true,
    };
  }
  const plain = stripMarkdownLite(trimmed);
  if (!plain || looksLikeRawBlob(plain)) {
    return {
      text: streaming ? "作答中…" : "点开查看",
      placeholder: true,
    };
  }
  return {
    text: summarizeText(plain, ANSWER_PREVIEW_LEN),
    placeholder: false,
  };
}

/** 粗剥 markdown 记号，只为折叠一行预览；展开仍走完整 Markdown。 */
function stripMarkdownLite(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*>+\s?/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/[*_~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** 疑似原始作答 blob（JSON / 质询应答标题块），折叠行不得原样露出。 */
function looksLikeRawBlob(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  if (t.startsWith("{") || t.startsWith("[")) return true;
  if (t.startsWith("## 质询应答")) return true;
  if (/^```(?:json)?\s*[\[{]/i.test(t)) return true;
  return false;
}
