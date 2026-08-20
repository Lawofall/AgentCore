import { Button, DecisionCard, DecisionCardIcon } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { notifyError } from "@/lib/toast";
import { useRunConfirmStore } from "@/stores/runConfirm";
import { Check, CheckCheck, Terminal, X } from "lucide-react";
import { useState } from "react";

const PREVIEW_CAP = 2000;

function clip(s: string): string {
  return s.length > PREVIEW_CAP ? `${s.slice(0, PREVIEW_CAP)}\n…（已截断）` : s;
}

/**
 * 用户直触 bash 的聊天内确认卡（取消 | 运行 | 本会话都允许）。
 * 布局对齐 {@link ApprovalPrompt}；挂在 ChatView 决策区底部。
 */
export function RunConfirmPrompt() {
  const pending = useRunConfirmStore((s) => s.pending);
  const decide = useRunConfirmStore((s) => s.decide);
  const markSessionAllowed = useRunConfirmStore((s) => s.markSessionAllowed);
  const [busy, setBusy] = useState(false);

  if (!pending) return null;

  const onRun = () => {
    decide("run");
  };

  const onAllowSession = () => {
    setBusy(true);
    const grant = window.fsApi?.grantSessionRun;
    if (!grant) {
      setBusy(false);
      notifyError("无法记录本会话放行（非桌面环境）");
      decide("cancel");
      return;
    }
    void grant()
      .then(() => {
        markSessionAllowed();
        decide("allow_session");
      })
      .catch((err) => {
        notifyError(err instanceof Error ? err.message : "本会话放行失败");
        decide("cancel");
      })
      .finally(() => setBusy(false));
  };

  const onCancel = () => {
    decide("cancel");
  };

  const preview = clip(pending.command);

  return (
    <div className="mx-4 mb-2 space-y-2">
      <DecisionCard tone="primary" animate className="mx-0">
        <div className="flex items-start gap-2">
          <DecisionCardIcon tone="primary">
            <Terminal size={16} />
          </DecisionCardIcon>
          <div className="min-w-0 flex-1">
            <p className="text-sm text-foreground">
              <span className="font-medium">在终端运行</span>
            </p>
            <SimpleTooltip label={pending.command}>
              <pre className="mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-xs text-muted-foreground">
                {preview}
              </pre>
            </SimpleTooltip>
          </div>
        </div>

        <div className="mt-2.5 flex flex-wrap items-center gap-1.5 pl-6">
          <Button
            variant="danger"
            icon={<X size={13} />}
            disabled={busy}
            onClick={onCancel}
          >
            取消
          </Button>
          <Button
            variant="primary"
            icon={<Check size={13} />}
            disabled={busy}
            onClick={onRun}
          >
            运行
          </Button>
          <Button
            variant="neutral"
            icon={<CheckCheck size={13} />}
            disabled={busy}
            onClick={onAllowSession}
          >
            本会话都允许
          </Button>
        </div>
      </DecisionCard>
    </div>
  );
}
