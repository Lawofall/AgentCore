/**
 * 协作图卡片 · 停止任务 — 停掉本执行下全部队员，保留对话与主 Agent。
 * 与状态条硬停（结束整轮）文案刻意区分：这里强调「队员停下、主 Agent 留下」。
 */

import { Button } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  useActiveTurnPhase,
  useConversationStore,
} from "@/stores/conversation";
import {
  projectRuntime,
  useExecutionScope,
  useExecutionStore,
} from "@/stores/execution";
import { useRunStopPendingStore } from "@/stores/runStopPending";
import { Square } from "lucide-react";
import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { workerRunsOf } from "./helpers";
import { isStoppableRunStatus, requestRunStop } from "./runStopActions";

export function GraphTeamStopControl({ className }: { className?: string }) {
  const messageId = useExecutionScope();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const turnPhase = useActiveTurnPhase();
  // projectRuntime layers fresh overlay objects over its cached base, so handing it
  // back from a selector makes every getSnapshot compare unequal — React then
  // re-renders forever (dev: "getSnapshot should be cached"; prod: React #185).
  // Reduce to primitives inside the selector and shallow-compare them.
  const { executionId, anyActive } = useExecutionStore(
    useShallow((s) => {
      const rt = messageId ? s.byId[messageId] : undefined;
      const execution = rt ? projectRuntime(rt) : null;
      return {
        executionId: execution?.id ?? null,
        anyActive:
          execution != null &&
          workerRunsOf(execution.runs).some((r) =>
            isStoppableRunStatus(r.status),
          ),
      };
    }),
  );
  const pendingAll = useRunStopPendingStore((s) =>
    executionId ? s.isPending(executionId, null) : false,
  );
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!executionId) return;
    useRunStopPendingStore.getState().clearAllIfIdle(executionId, anyActive);
  }, [executionId, anyActive]);

  // 整轮已在停：再挂「停止任务」会打 run-stop，驱动往往已经不在了，toast
  // 「没有停下任何工作」像失败。输入框硬停才是这条路径；两档停机不合并。
  if (!conversationId || !executionId || !anyActive || turnPhase === "stopping")
    return null;

  const busy = pendingAll || submitting;
  const label = busy ? "停止请求中…" : "停止任务";

  return (
    <div className={className}>
      <SimpleTooltip
        label={
          busy
            ? "停止请求已发出，队员状态会随后更新（不会假装已经停完）"
            : "停掉这支团队所有在跑/排队的队员；主 Agent 留下继续交代（不是结束整轮对话）"
        }
      >
        <Button
          type="button"
          variant="ghost"
          className="h-7 gap-1 rounded-full border border-border/80 bg-card/95 px-2.5 text-xs font-medium text-muted-foreground shadow-sm backdrop-blur hover:bg-destructive/10 hover:text-destructive"
          icon={<Square size={12} className="shrink-0" />}
          disabled={busy}
          aria-label={label}
          onClick={async () => {
            if (busy) return;
            setSubmitting(true);
            try {
              await requestRunStop({
                conversationId,
                executionId,
                runId: null,
                scope: "team",
              });
            } finally {
              setSubmitting(false);
            }
          }}
        >
          {label}
        </Button>
      </SimpleTooltip>
    </div>
  );
}
