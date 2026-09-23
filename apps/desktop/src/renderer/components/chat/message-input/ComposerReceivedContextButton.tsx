import { ReceivedContextDialog } from "@/components/chat/ReceivedContext";
import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useModels } from "@/hooks/useModels";
import { useComposerActiveProfile } from "@/lib/composerModelProfile";
import { cn } from "@/lib/utils";
import {
  WINDOW_FILL_WARN_RATIO,
  captainCompletedModel,
  catalogContextLength,
  sessionWindowPrompt,
  windowFill,
  windowFillLabel,
} from "@/lib/windowFill";
import {
  type Message,
  activeRuntime,
  assistantProjectionId,
  useConversationStore,
} from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import type { ContextBlockWire, ProcessStep } from "@/types/events";
import { useEffect, useState } from "react";

const EMPTY_BLOCKS: ContextBlockWire[] = [];
const EMPTY_PROCESS: ProcessStep[] = [];
const EMPTY_FRAMES: [] = [];

function lastAssistant(messages: Message[]): Message | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return messages[i];
  }
  return null;
}

/** 与底排 Lucide 工具同几何：24 画布、16 绘制、stroke 2（＋/语音/发送都是 `size={16}`）。 */
function WindowFillRing({
  ratio,
  warn,
}: {
  ratio: number | null;
  warn: boolean;
}) {
  const size = 16;
  const r = 10;
  const c = 2 * Math.PI * r;
  const filled = ratio == null ? 0 : Math.min(Math.max(ratio, 0), 1);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      className="shrink-0"
    >
      <circle
        cx="12"
        cy="12"
        r={r}
        fill="none"
        className="stroke-muted-foreground/25"
        strokeWidth="2"
      />
      <circle
        cx="12"
        cy="12"
        r={r}
        fill="none"
        className={warn ? "stroke-warning" : "stroke-current"}
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={`${filled * c} ${c}`}
        transform="rotate(-90 12 12)"
      />
    </svg>
  );
}

/**
 * 整块输入框外侧右边：当前这场 CEO「收到的上下文」。气泡底栏仍留当时快照，这里不替代。
 * 铬条尺寸与＋/语音同档 `md`，宿主用底排行盒对齐，不跟卡片边框底边对齐。
 * 第一帧 `run_context` 写进最后一条助手泡就亮，不等停笔；弹窗开着跟 blocks / process 变长。
 * 窗口环 = 本场一条水位 / 目录 context_length。每次 CEO 请求 usage 返回就
 * 改写这一个数；本场还没有观测时，用最近一条已落盘的水位。
 */
export function ComposerReceivedContextButton() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const messages = useConversationStore((s) => activeRuntime(s).messages);
  const measured = useConversationStore((s) => activeRuntime(s).ceoWindowTokens);
  const message = lastAssistant(messages);
  const projectionId = message ? assistantProjectionId(message) : null;
  const frames = useExecutionStore((s) =>
    projectionId ? s.byId[projectionId]?.frames : undefined,
  );
  const { data: catalog } = useModels();
  const profile = useComposerActiveProfile();
  const [open, setOpen] = useState(false);
  const blocks = message?.captainContext ?? EMPTY_BLOCKS;
  const process = message?.process ?? EMPTY_PROCESS;
  const used = sessionWindowPrompt(measured, messages);
  const windowTokens = catalogContextLength(catalog?.models ?? [], {
    modelId: captainCompletedModel(frames ?? EMPTY_FRAMES),
    slot: profile?.main ?? null,
  });
  const fill =
    used != null && windowTokens != null
      ? windowFill(used, windowTokens)
      : null;
  const label = windowFillLabel(fill, used);

  // biome-ignore lint/correctness/useExhaustiveDependencies: conversationId is the reset key — close the dialog when the chat changes.
  useEffect(() => {
    setOpen(false);
  }, [conversationId]);

  if (blocks.length === 0) return null;

  return (
    <>
      <SimpleTooltip label={label}>
        <IconButton
          size="md"
          aria-label="收到的上下文"
          data-testid="composer-received-context"
          data-window-percent={fill ? String(fill.percent) : undefined}
          className={cn(
            fill && fill.ratio >= WINDOW_FILL_WARN_RATIO && "text-warning",
          )}
          onClick={() => setOpen(true)}
        >
          <WindowFillRing
            ratio={fill?.ratio ?? null}
            warn={Boolean(fill && fill.ratio >= WINDOW_FILL_WARN_RATIO)}
          />
        </IconButton>
      </SimpleTooltip>
      <ReceivedContextDialog
        blocks={blocks}
        process={process}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}
