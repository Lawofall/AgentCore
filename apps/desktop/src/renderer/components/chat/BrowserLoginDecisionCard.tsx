import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
/**
 * Shared「需要你登录 / 已登录，继续」surface for:
 * - worker ``escalate(browser_login=true)`` (hot-path EscalationCard)
 * - CEO ``ask_user(browser_login=true)`` (cold-path ResumePrompt)
 *
 * Reveals the right-dock browser shell only when the user clicks「打开浏览器」.
 */
import { escalationWaitNote } from "@/components/chat/escalationWaitCopy";
import { Button, DecisionCard, DecisionCardIcon } from "@/components/ui";
import { useSidePanelStore } from "@/stores/sidePanel";
import {
  ArrowRight,
  Check,
  Loader2,
  LogIn,
  OctagonX,
  Radio,
} from "lucide-react";

export type BrowserLoginSubmitKind = "logged_in" | "use_assumption" | "stop";

export function BrowserLoginDecisionCard({
  roleLabel,
  question,
  assumption,
  conversationId,
  timeoutSeconds,
  busy,
  submitting,
  onLoggedIn,
  onUseAssumption,
  onStop,
  kindTag,
}: {
  roleLabel: string;
  question: string;
  assumption?: string;
  conversationId: string | null;
  /** Call-site key (escalation.id / checkpointId); retained for API compatibility. */
  revealKey: string;
  /** 后端下发的等待上限；缺省 = 一直等（冷路挂起本就没有墙钟）。 */
  timeoutSeconds?: number | null;
  busy: boolean;
  submitting: BrowserLoginSubmitKind | null;
  onLoggedIn: () => void;
  onUseAssumption?: () => void;
  onStop?: () => void;
  kindTag?: string;
}) {
  return (
    <DecisionCard tone="primary" animate>
      <div className="flex items-start gap-2">
        <DecisionCardIcon tone="primary">
          <LogIn size={16} />
        </DecisionCardIcon>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <p className="min-w-0 flex-1 text-xs font-medium text-primary">
              {roleLabel} · 需要你登录
              {kindTag ? ` · ${kindTag}` : ""}
            </p>
            <ManualHelpLink to={MANUAL_HELP.control} />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            在浏览器里完成登录后，点「已登录，继续」
          </p>
          <p className="mt-0.5 whitespace-pre-wrap text-sm text-foreground">
            {question}
          </p>
          {assumption ? (
            <p className="mt-2 rounded-lg bg-card/60 px-2.5 py-1.5 text-xs text-muted-foreground">
              {escalationWaitNote({ assumption, timeoutSeconds })}
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center justify-end gap-1.5 pl-6">
        {conversationId && (
          <Button
            variant="neutral"
            disabled={busy}
            onClick={() => useSidePanelStore.getState().showBrowser()}
            icon={<Radio size={13} />}
          >
            打开浏览器
          </Button>
        )}
        <Button
          variant="primary"
          disabled={busy}
          onClick={onLoggedIn}
          icon={
            submitting === "logged_in" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Check size={13} />
            )
          }
        >
          已登录，继续
        </Button>
        {onUseAssumption ? (
          <Button
            variant="neutral"
            disabled={busy}
            onClick={onUseAssumption}
            icon={
              submitting === "use_assumption" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <ArrowRight size={13} />
              )
            }
          >
            按假设继续
          </Button>
        ) : null}
        {onStop ? (
          <Button
            variant="danger"
            disabled={busy}
            onClick={onStop}
            icon={
              submitting === "stop" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <OctagonX size={13} />
              )
            }
          >
            取消
          </Button>
        ) : null}
      </div>
    </DecisionCard>
  );
}
