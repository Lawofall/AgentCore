/**
 * 「这张卡已经处理过了」结果态收口条（冷 resume 幂等成功 · EPHEMERAL `resume_settled`）。
 *
 * 用户点了「继续」，服务端却告诉他这张卡的帧早被上一次续跑吃掉了。这**不是故障**——多端
 * 同权下另一端先处理、或本端上一次点击的 SSE 断在半路，都会走到这里，所以一律用中性 /
 * 信息态，绝不用红色告警的视觉语言。卡不能就这么消失（消失会让人以为是自己点掉的），
 * 于是在原位留一条只读收口，说清**何时以什么决策**结的、回合现在到哪一步，几秒后退场。
 *
 * 说不出的两件事一个字都不提：**谁**处理的（线材里没有处理方），以及回合状态之外的推测。
 *
 * `turn_status=running` 不进这里：那条连接紧接着就是续跑的实时流，用户看见 AI 在继续写就是
 * 最好的交代，再压一条「已处理」只会喧宾夺主。
 */
import { DecisionCard, DecisionCardIcon } from "@/components/ui";
import {
  resumeSettledHeadline,
  resumeSettledTurnCopy,
} from "@/lib/resumeSettled";
import { useConversationStore } from "@/stores/conversation";
import {
  INTERACTION_CARD_NAME,
  useInteractionStore,
} from "@/stores/interactions";
import { Info } from "lucide-react";
import { useEffect, useState } from "react";

/** 收口条停留时长——与「已由另一端处理」同款，够看见又不常驻决策区。 */
const NOTICE_TTL_MS = 8_000;

type ResumeSettledNotice = {
  id: string;
  label: string;
  headline: string;
  turnCopy: string;
};

export function ResumeSettledNotices() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const [notices, setNotices] = useState<ResumeSettledNotice[]>([]);

  useEffect(() => {
    setNotices([]);
    if (!conversationId) return;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const drop = (id: string) =>
      setNotices((cur) => cur.filter((n) => n.id !== id));
    const unsub = useInteractionStore.subscribe((state, prev) => {
      for (const [id, entry] of state.byId) {
        if (entry.conversationId !== conversationId) continue;
        const settled = entry.resumeSettled;
        if (!settled) continue;
        // 只在「刚落定」这一拍出条：切会话回来、重放整段都不该再闪一遍旧收口。
        if (prev.byId.get(id)?.resumeSettled) continue;
        if (settled.turnStatus === "running") continue;
        setNotices((cur) =>
          cur.some((n) => n.id === id)
            ? cur
            : [
                ...cur,
                {
                  id,
                  label: INTERACTION_CARD_NAME[entry.kind] ?? "确认",
                  headline: resumeSettledHeadline(settled),
                  turnCopy: resumeSettledTurnCopy(settled.turnStatus),
                },
              ],
        );
        const timer = setTimeout(() => {
          timers.delete(timer);
          drop(id);
        }, NOTICE_TTL_MS);
        timers.add(timer);
      }
    });
    return () => {
      unsub();
      for (const timer of timers) clearTimeout(timer);
    };
  }, [conversationId]);

  if (notices.length === 0) return null;

  return (
    <div className="mx-4 mb-2 space-y-2" data-testid="resume-settled">
      {notices.map((notice) => (
        <DecisionCard key={notice.id} tone="neutral" className="mx-0">
          <div className="flex items-start gap-2">
            <DecisionCardIcon tone="neutral">
              <Info size={16} />
            </DecisionCardIcon>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-foreground">
                <span className="font-medium">{notice.label}</span>
                <span className="text-muted-foreground"> · </span>
                <span className="font-medium">{notice.headline}</span>
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {notice.turnCopy}
              </p>
            </div>
          </div>
        </DecisionCard>
      ))}
    </div>
  );
}
