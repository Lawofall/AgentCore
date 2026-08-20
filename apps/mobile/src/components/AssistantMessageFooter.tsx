import { InteractionSheet } from "@/components/InteractionSheet";
import { Modal } from "@/components/Modal";
import { CONTEXT_CHANNEL_LABEL } from "@/components/assistantLabels";
import { FINISH_REASON_LABELS, FINISH_REASON_META } from "@/lib/errors";
import {
  type MessageCopyMode,
  copyText,
  formatMessageExport,
} from "@/lib/messageExport";
import {
  type SupportDiagnosticIds,
  formatSupportDiagnosticText,
} from "@/lib/supportDiagnostics";
import { formatDuration, formatMessageTime } from "@/lib/time";
import type {
  ContextBlockWire,
  ProcessStep,
  UsageBreakdown,
} from "@agentcore/contract-types";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheUsageDisplay,
} from "@agentcore/protocol-fold-kit";
import { Check, Copy, MoreHorizontal } from "lucide-react";
import { useState } from "react";
import "./AssistantMessageFooter.css";

function formatCompact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function UsageDetailRows({ usage }: { usage: UsageBreakdown }) {
  const cache = cacheUsageDisplay(usage);
  return (
    <div className="amf-usage-detail">
      <div className="amf-usage-row">
        <span>输入</span>
        <span className="amf-usage-val">{formatCompact(usage.input)}</span>
      </div>
      {cache.billedAsMiss ? (
        <div className="amf-usage-row">
          <span>{CACHE_BILLED_AS_MISS_LABEL}</span>
          <span className="amf-usage-val">
            {formatCompact(cache.cacheMiss)}
          </span>
        </div>
      ) : (
        <>
          <div className="amf-usage-row">
            <span>缓存命中</span>
            <span className="amf-usage-val">
              {formatCompact(cache.cacheHit)}
              {cache.hitRatePercent != null
                ? ` · ${cache.hitRatePercent}%`
                : ""}
            </span>
          </div>
          <div className="amf-usage-row">
            <span>缓存未命中</span>
            <span className="amf-usage-val">
              {formatCompact(cache.cacheMiss)}
            </span>
          </div>
        </>
      )}
      <div className="amf-usage-row">
        <span>输出</span>
        <span className="amf-usage-val">{formatCompact(usage.output)}</span>
      </div>
      {usage.reasoning > 0 && (
        <div className="amf-usage-row">
          <span>思考</span>
          <span className="amf-usage-val">
            {formatCompact(usage.reasoning)}
          </span>
        </div>
      )}
    </div>
  );
}

/** Signal-only footer meta (cost / rounds / duration / clock); token detail lives in ⋯ Sheet. */
function UsageSummary({
  rounds,
  costText,
  durationMs,
  clockIso,
}: {
  rounds?: number | null;
  costText?: string | null;
  durationMs?: number | null;
  clockIso?: string | null;
}) {
  const durationText =
    durationMs != null && durationMs > 0 ? formatDuration(durationMs) : null;
  const clockLabel = clockIso ? formatMessageTime(clockIso) : "";
  const parts: string[] = [];
  if (costText) parts.push(costText);
  if (rounds != null && rounds > 1) parts.push(`${rounds} 轮`);
  if (durationText) parts.push(`用时 ${durationText}`);
  if (clockLabel) parts.push(clockLabel);
  if (parts.length === 0) return null;

  return (
    <div className="amf-usage" data-testid="assistant-usage-summary">
      {parts.join(" · ")}
    </div>
  );
}

/** Touch-native copy picker — mirrors desktop Copy dropdown (仅交付 / 含过程). */
function CopyModeSheet({
  onPick,
  onClose,
}: {
  onPick: (mode: MessageCopyMode) => void;
  onClose: () => void;
}) {
  return (
    <Modal className="sheet" onClose={onClose} label="复制">
      <div className="sheet-title">复制</div>
      <button
        type="button"
        className="sheet-item"
        data-testid="copy-mode-deliverable"
        onClick={() => onPick("deliverable")}
      >
        仅交付
      </button>
      <button
        type="button"
        className="sheet-item"
        data-testid="copy-mode-with-process"
        onClick={() => onPick("with_process")}
      >
        含过程
      </button>
      <button
        type="button"
        className="sheet-item sheet-cancel"
        onClick={onClose}
      >
        取消
      </button>
    </Modal>
  );
}

/** CEO「收到的上下文」明细列表（含 system；对齐桌面 ReceivedContextDialog）。 */
function ReceivedContextBlocks({ blocks }: { blocks: ContextBlockWire[] }) {
  return (
    <div className="recv-list" data-testid="received-context-blocks">
      {blocks.map((b, i) => (
        <div key={`${b.channel}-${i}`} className="recv-item">
          <div className="recv-head">
            <span className="recv-channel">
              {CONTEXT_CHANNEL_LABEL[b.channel] ?? b.channel}
            </span>
            {b.heading && <span className="recv-heading">{b.heading}</span>}
          </div>
          {b.body && <pre className="recv-body">{b.body}</pre>}
          {b.files.length > 0 && (
            <div className="recv-files">
              {b.files.map((f) => (
                <span key={f} className="recv-file">
                  {f}
                </span>
              ))}
            </div>
          )}
          {b.truncated && (
            <div className="recv-trunc">已截断（完整内容已传给 AI）</div>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Assistant bubble footer — copy strategy + usage/cost/duration hierarchy.
 * 主行：无边框图标 Copy / MoreHorizontal（对齐桌面）；有过程时 Copy → Action Sheet 分档。
 * ⋯ Sheet：收到的上下文 / 用量 / 收尾 / 排查包。赞踩 / 收藏：手机尚无 client API，不做假 UI。
 */
export function AssistantMessageFooter({
  content,
  process,
  supportIds,
  captainContext,
  usage,
  rounds,
  costText,
  durationMs,
  clockIso,
  finishReason,
  failureNotice,
  isStreaming = false,
}: {
  content: string;
  process?: ProcessStep[];
  supportIds?: SupportDiagnosticIds;
  /** CEO 侧 run_context；非空时「更多」露出入口，段数用全量 length（含 system）。 */
  captainContext?: ContextBlockWire[];
  usage?: UsageBreakdown | null;
  rounds?: number | null;
  costText?: string | null;
  durationMs?: number | null;
  clockIso?: string | null;
  finishReason?: string | null;
  /** Empty-failure visible notice for copy when content is blank. */
  failureNotice?: string | null;
  isStreaming?: boolean;
}) {
  const [copied, setCopied] = useState<MessageCopyMode | "support" | null>(
    null,
  );
  const [moreOpen, setMoreOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [copySheetOpen, setCopySheetOpen] = useState(false);

  const supportText = supportIds ? formatSupportDiagnosticText(supportIds) : "";
  const hasContent =
    !!content.trim() ||
    !!(process && process.length > 0) ||
    !!failureNotice?.trim();
  const hasProcess = (process?.length ?? 0) > 0;
  const finishLabel = finishReason
    ? (FINISH_REASON_LABELS[finishReason] ?? null)
    : null;
  const contextBlocks = captainContext ?? [];
  const hasContext = contextBlocks.length > 0;
  const hasMore =
    hasContext ||
    !!usage ||
    !!finishLabel ||
    !!supportText ||
    (rounds != null && rounds > 1);

  const onCopy = async (mode: MessageCopyMode) => {
    setCopySheetOpen(false);
    const text = formatMessageExport(content, process, mode, {
      failureNotice,
    });
    if (await copyText(text)) {
      setCopied(mode);
      window.setTimeout(() => setCopied(null), 1500);
    }
  };

  const onCopyTrigger = () => {
    if (hasProcess) {
      setCopySheetOpen(true);
      return;
    }
    void onCopy("deliverable");
  };

  const copyDone = copied === "deliverable" || copied === "with_process";

  const copyBtn = (
    <button
      type="button"
      className="amf-icon-btn"
      onClick={onCopyTrigger}
      data-testid="assistant-footer-copy"
      aria-label={copyDone ? "已复制" : "复制"}
    >
      {copyDone ? (
        <Check size={16} strokeWidth={1.75} />
      ) : (
        <Copy size={16} strokeWidth={1.75} />
      )}
    </button>
  );

  const copySheet = copySheetOpen ? (
    <CopyModeSheet
      onPick={(mode) => void onCopy(mode)}
      onClose={() => setCopySheetOpen(false)}
    />
  ) : null;

  const contextSheet = contextOpen ? (
    <InteractionSheet
      title="收到的上下文"
      label="收到的上下文"
      onCollapse={() => setContextOpen(false)}
      footer={
        <button
          type="button"
          className="amf-sheet-done"
          onClick={() => setContextOpen(false)}
        >
          完成
        </button>
      }
    >
      <ReceivedContextBlocks blocks={contextBlocks} />
    </InteractionSheet>
  ) : null;

  // Streaming: only lightweight copy when there is body text (usage meaningless mid-stream).
  if (isStreaming) {
    if (!hasContent || !content.trim()) return null;
    return (
      <div data-testid="assistant-message-footer">
        <div className="amf">
          <div className="amf-actions">{copyBtn}</div>
        </div>
        {copySheet}
      </div>
    );
  }

  if (
    !hasContent &&
    !supportText &&
    !usage &&
    !costText &&
    !durationMs &&
    !hasContext
  ) {
    return null;
  }

  const onCopySupport = async () => {
    if (!supportText) return;
    if (await copyText(supportText)) {
      setCopied("support");
      window.setTimeout(() => setCopied(null), 1500);
    }
  };

  const openContext = () => {
    setMoreOpen(false);
    setContextOpen(true);
  };

  return (
    <div data-testid="assistant-message-footer">
      <div className="amf">
        <div className="amf-actions">
          {hasContent && copyBtn}
          {hasMore && (
            <button
              type="button"
              className="amf-icon-btn"
              onClick={() => setMoreOpen(true)}
              data-testid="assistant-footer-more"
              aria-label="更多"
            >
              <MoreHorizontal size={16} strokeWidth={1.75} />
            </button>
          )}
        </div>
        <UsageSummary
          rounds={rounds}
          costText={costText}
          durationMs={durationMs}
          clockIso={clockIso}
        />
      </div>
      {copySheet}
      {moreOpen && (
        <InteractionSheet
          title="消息详情"
          label="消息详情"
          onCollapse={() => setMoreOpen(false)}
          footer={
            <button
              type="button"
              className="amf-sheet-done"
              onClick={() => setMoreOpen(false)}
            >
              完成
            </button>
          }
        >
          {hasContext && (
            <button
              type="button"
              className="amf-sheet-nav"
              data-testid="received-context-menu-item"
              onClick={openContext}
            >
              收到的上下文 · {contextBlocks.length} 段
            </button>
          )}
          {usage && (
            <>
              <div className="amf-sheet-label">用量详情</div>
              <UsageDetailRows usage={usage} />
              {rounds != null && rounds > 1 && (
                <div className="amf-usage-row amf-usage-row-pad">
                  <span>ReAct 轮次</span>
                  <span className="amf-usage-val">{rounds} 轮</span>
                </div>
              )}
            </>
          )}
          {finishLabel && (
            <>
              <div className="amf-sheet-label">收尾原因</div>
              <p className="amf-sheet-text">{finishLabel}</p>
            </>
          )}
          {supportText && (
            <button
              type="button"
              className="amf-btn amf-btn-block"
              onClick={() => void onCopySupport()}
            >
              {copied === "support" ? "已复制" : "复制排查包"}
            </button>
          )}
        </InteractionSheet>
      )}
      {contextSheet}
    </div>
  );
}

/** 「曾中断恢复」：崩溃重驱把这条回合原地跑完了（D5 归属原回合）。诚实优先——
 *  成果虽然完整，也不许静默假装它没断过。桌面同款文案。 */
export function RecoveredChip() {
  return (
    <div
      className="finish-chip"
      data-testid="recovered-chip"
      title="本回合中途中断，系统已自动接着跑完；成果就在这条消息里。"
    >
      曾中断恢复
    </div>
  );
}

/** Top-of-bubble chip for abnormal turn endings. */
export function FinishReasonChip({
  reason,
  diagnosisLabel,
}: {
  reason: string | null | undefined;
  diagnosisLabel?: string;
}) {
  const meta = reason ? FINISH_REASON_META[reason] : undefined;
  if (!meta) return null;
  const label =
    reason === "degraded" && diagnosisLabel ? diagnosisLabel : meta.label;
  return (
    <div className="finish-chip" data-testid="finish-reason-chip">
      {label}
    </div>
  );
}
