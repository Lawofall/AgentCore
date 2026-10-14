/**
 * 「只停这个人 / 只改这个人的方向」—— 两个按人干预动作的唯一实现，桌面右坞 run
 * 详情挂载。图节点不挂这两枚按钮（点节点即开本详情）。
 *
 * 调用方用 `isLiveRunStatus` **终局整条不挂载**（不留空 wrapper）。仍挂载时
 * （running / pending），不可用动作变灰并说明原因——排队改方向就是这一类，不要
 * 藏成「按钮不见了」。整轮 `turnPhase === "stopping"` 除外：按人停已被输入框
 * 硬停覆盖，再挂会打空 run-stop。判定与文案由 `protocol-fold-kit/runIntervene`
 * 给出，不在本文件另写 status 表。
 *
 * 动作是标题行右侧的纯图标；可见名字只在 aria-label / tooltip。点不动的原因仍
 * 写在标题下面（右坞有空间，不必等 hover）。
 */

import { Button, IconButton, Textarea } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { submitRunRedirect } from "@/services/runRedirect";
import type { RunStatus } from "@/stores/execution";
import {
  type InterveneGate,
  interveneAckText,
  runRedirectGate,
} from "@agentcore/protocol-fold-kit";
import { RotateCcw } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { toast } from "sonner";

export interface RunInterveneControlsProps {
  conversationId: string;
  executionId: string;
  runId: string;
  runStatus: RunStatus;
  /** 队员角色名——进改方向草稿开头。 */
  role: string;
  /** 本幕是否开放改方向（辩论幕恒 false → 该按钮整体不渲染）。 */
  redirectCapable: boolean;
  /** 标题行左侧（角色 / 接手 / 权限快照 / 终态徽章）。 */
  headerStart: ReactNode;
  className?: string;
}

const REDIRECT_LABEL = "改这一段";
const REDIRECT_PLACEHOLDER = "具体、可执行的修改方向…";

/** 改方向草稿开头：恒用角色名模板，不扫产出正文。 */
function seedRedirectDraft(role: string): string {
  return `请按以下方向调整「${role}」的产出：`;
}

export function RunInterveneControls({
  conversationId,
  executionId,
  runId,
  runStatus,
  role,
  redirectCapable,
  headerStart,
  className,
}: RunInterveneControlsProps) {
  const [draft, setDraft] = useState("");
  const [composerOpen, setComposerOpen] = useState(false);

  const redirectGate = runRedirectGate(runStatus);

  const openComposer = () => {
    setDraft(seedRedirectDraft(role));
    setComposerOpen(true);
  };

  const closeComposer = () => {
    setComposerOpen(false);
  };

  const redirectTip = redirectGate.enabled
    ? "取消这一段在做的事，带着新方向再做一遍（会重新花时间和钱）"
    : redirectGate.reason;
  const panelReason = redirectCapable ? (redirectGate.reason ?? null) : null;

  return (
    <div className={cn("mb-4 space-y-2", className)}>
      <div className="flex items-center gap-2">
        {headerStart}
        {redirectCapable && (
          <span className="flex shrink-0 items-center gap-0.5">
            <GatedIconButton
              gate={redirectGate}
              label={REDIRECT_LABEL}
              tip={redirectTip}
              icon={<RotateCcw size={13} />}
              tone="primary"
              onClick={openComposer}
            />
          </span>
        )}
      </div>

      {composerOpen && (
        <div className="space-y-2 border-t border-border pt-2">
          <RunRedirectComposer
            conversationId={conversationId}
            executionId={executionId}
            runId={runId}
            draft={draft}
            onDraftChange={setDraft}
            onDone={closeComposer}
          />
        </div>
      )}

      {/* 右坞有空间就把原因直接写出来，不必等用户去 hover 才知道为什么点不动。 */}
      {panelReason && (
        <p className="text-xs leading-snug text-muted-foreground">
          {panelReason}
        </p>
      )}
    </div>
  );
}

/**
 * 不可用走 `aria-disabled` 而非 `disabled`：原生 disabled 按钮不派发鼠标事件，
 * tooltip 就永远说不出「为什么不能」——那正是要修的毛病。`disabled` 只留给
 * 「停止请求中…」这类真·在飞态。
 */
function GatedIconButton({
  gate,
  label,
  tip,
  icon,
  tone,
  busy = false,
  onClick,
}: {
  gate: InterveneGate;
  label: string;
  tip: ReactNode;
  icon: ReactNode;
  tone: "primary" | "destructive";
  busy?: boolean;
  onClick: () => void | Promise<void>;
}) {
  const unavailable = !gate.enabled;
  const toneClass =
    tone === "primary"
      ? "text-primary hover:bg-primary/10 hover:text-primary"
      : "text-muted-foreground hover:bg-destructive/10 hover:text-destructive";
  return (
    <SimpleTooltip label={tip}>
      <IconButton
        type="button"
        className={
          unavailable
            ? "cursor-not-allowed text-muted-foreground/60 hover:bg-transparent hover:text-muted-foreground/60"
            : toneClass
        }
        disabled={busy}
        aria-busy={busy || undefined}
        aria-disabled={unavailable || undefined}
        aria-label={unavailable ? `${label}（${gate.reason}）` : label}
        title={unavailable ? (gate.reason ?? undefined) : undefined}
        onClick={(e) => {
          e.stopPropagation();
          if (unavailable || busy) return;
          void onClick();
        }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {icon}
      </IconButton>
    </SimpleTooltip>
  );
}

/** 写改法 + 提交（提交语义与提示文案只此一份）。 */
function RunRedirectComposer({
  conversationId,
  executionId,
  runId,
  draft,
  onDraftChange,
  onDone,
}: {
  conversationId: string;
  executionId: string;
  runId: string;
  draft: string;
  onDraftChange: (text: string) => void;
  onDone: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  return (
    <div className="space-y-2">
      <Textarea
        className="min-h-[4.5rem] w-full resize-y text-sm"
        value={draft}
        onChange={(e) => onDraftChange(e.target.value)}
        placeholder={REDIRECT_PLACEHOLDER}
      />
      <div className="flex flex-wrap gap-2">
        <Button
          variant="primary"
          className="h-7"
          disabled={submitting || !draft.trim()}
          onClick={async () => {
            if (!draft.trim()) return;
            setSubmitting(true);
            try {
              const ack = await submitRunRedirect(conversationId, {
                executionId,
                runId,
                feedback: draft.trim(),
              });
              // 受理与否由引擎回答：够不着这个 run 时**什么都没发生**，此时不许说
              // 「已改方向」，也别关掉草稿——用户可能想改去别处。
              if (!ack.accepted) {
                toast.warning("没有改到这位队员", {
                  description: interveneAckText(ack),
                });
                return;
              }
              // 诚实：后端收到即取消这名队员在飞的工作，并优先带现场热续跑
              // （接不上才同角色换人重做）。别说成「还在排队、什么都没发生」
              // ——用户会据此以为可以再点一次。
              toast.success("已改方向：这名队员的在飞工作已取消", {
                description:
                  "正带着你的新方向重跑；接不上现场就从头重做，这段要重新花时间和钱。",
              });
              onDone();
            } catch {
              toast.error("提交失败，请稍后重试");
            } finally {
              setSubmitting(false);
            }
          }}
        >
          提交改方向
        </Button>
        <Button variant="ghost" className="h-7" onClick={onDone}>
          取消
        </Button>
      </div>
    </div>
  );
}
