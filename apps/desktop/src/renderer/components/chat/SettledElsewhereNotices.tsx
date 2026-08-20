/**
 * 「已由另一端处理」收口条（云对话多端同权 B2 · P1 · 验收 2）。
 *
 * 手机（或另一台桌面）拍板后，本端那张卡不能**直接消失**——消失会让用户以为是自己点掉的，
 * 甚至以为界面出错了。这里在卡原来的位置留一条只读收口，说清「谁处理的、AI 已经续跑」，
 * 几秒后自行退场。永久痕迹另有归宿：时间线上 required 时刻的 {@link ApprovalTrace} 一族。
 *
 * 只收「本端此刻正显示着」的卡：条目须在这次订阅回调的 **prev 快照里仍是 pending**，且
 * 带上 `settledElsewhere`（store 侧已排除重放段与 journal 水合）。因此重连整段重放不会
 * 闪出一堆旧收口，切走再切回也不会把别人的会话带过来。
 *
 * 重连快照水合（`hydratePending`）是第三个写 `settledElsewhere` 的地方：暂停前就已亮出、
 * 来源也确实问过的卡不在快照里，会就地留桩终态——那张卡真是在另一端结掉的，这里正该出一条
 * 收口。冷启动的新标签页 prev 不是 pending，不会替旧会话补喊。
 *
 * 「pending」这道闸还顺带分掉了另一种情形：用户自己点下去、回执才说「已经结了」
 * （`settledByReceipt`）——那一下已经当场给过提示，随后到的收口帧只该去时间线补一句归属，
 * 不必在决策区再说一遍。
 */
import { DecisionCard, DecisionCardIcon } from "@/components/ui";
import { useConversationStore } from "@/stores/conversation";
import { toolLabel } from "@/stores/execution/types";
import {
  INTERACTION_CARD_NAME,
  type InteractionEntry,
  useInteractionStore,
} from "@/stores/interactions";
import { Smartphone } from "lucide-react";
import { useEffect, useState } from "react";

/** 收口条停留时长——够看见，又不至于在决策区常驻。 */
const NOTICE_TTL_MS = 8_000;

type SettledNotice = { id: string; label: string };

export function settledElsewhereLabel(entry: InteractionEntry): string {
  const name = INTERACTION_CARD_NAME[entry.kind] ?? "确认";
  if (entry.kind !== "approval") return name;
  const raw =
    typeof entry.payload.tool_name === "string" ? entry.payload.tool_name : "";
  const tool = toolLabel(raw) || raw;
  return tool ? `${name} · ${tool}` : name;
}

export function SettledElsewhereNotices() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const [notices, setNotices] = useState<SettledNotice[]>([]);

  useEffect(() => {
    setNotices([]);
    if (!conversationId) return;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const drop = (id: string) =>
      setNotices((cur) => cur.filter((n) => n.id !== id));
    const unsub = useInteractionStore.subscribe((state, prev) => {
      for (const [id, entry] of state.byId) {
        if (entry.conversationId !== conversationId) continue;
        if (entry.status !== "resolved" || !entry.settledElsewhere) continue;
        // 本端上一刻还显示着它 → 这一下的消失需要交代。
        if (prev.byId.get(id)?.status !== "pending") continue;
        const label = settledElsewhereLabel(entry);
        setNotices((cur) =>
          cur.some((n) => n.id === id) ? cur : [...cur, { id, label }],
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
    <div className="mx-4 mb-2 space-y-2" data-testid="settled-elsewhere">
      {notices.map((notice) => (
        <DecisionCard key={notice.id} tone="neutral" className="mx-0">
          <div className="flex items-start gap-2">
            <DecisionCardIcon tone="neutral">
              <Smartphone size={16} />
            </DecisionCardIcon>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-foreground">
                <span className="font-medium">{notice.label}</span>
                <span className="text-muted-foreground"> · </span>
                <span className="font-medium">已由另一端处理</span>
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                AI 已继续，这里无需再操作。
              </p>
            </div>
          </div>
        </DecisionCard>
      ))}
    </div>
  );
}
