import { ApprovalCard } from "@/components/chat/ApprovalPrompt";
import { toolLabelZh } from "@/components/chat/toolLabelsZh";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ApprovalView } from "@/stores/interactions";
import type { ApprovalDecision } from "@/types/events";
import { Check } from "lucide-react";
import { useState } from "react";
import { ManualDemoRetryBar } from "./ManualDemoRetryBar";

const DEMO_APPROVAL: ApprovalView = {
  approvalId: "manual-demo-approval",
  conversationId: "manual-demo",
  toolCallId: "manual-demo-tc",
  toolName: "file_write",
  arguments: {
    path: "reports/week-summary.md",
    content: "# 周报摘要\n\n本周成本下降 12%，异常点已标注。\n",
  },
  resolving: false,
};

function approvalDemoLabel(decision: ApprovalDecision): string {
  const tool = toolLabelZh(DEMO_APPROVAL.toolName);
  if (decision === "deny") return `已拒绝 · ${tool}`;
  if (decision === "approve_always") return `已允许（本轮内都允许）· ${tool}`;
  if (decision === "approve_always_files") {
    return `已允许（本轮内所有文件改动）· ${tool}`;
  }
  return `已允许 · ${tool}`;
}

/**
 * 手册「真组件预览」：工具审批卡。
 * 复用 {@link ApprovalCard}；点按后换成时间线同形痕迹，不打 API，可再试。
 */
export function ManualApprovalCardPreview() {
  const [decision, setDecision] = useState<ApprovalDecision | null>(null);

  return (
    <TooltipProvider>
      <div
        className="w-full max-w-3xl"
        data-manual-demo-phase={decision ? "settled" : "live"}
      >
        {decision ? (
          <>
            <div
              className="flex items-center gap-1.5 text-xs text-muted-foreground"
              data-testid="manual-approval-demo-trace"
            >
              <Check size={12} className="shrink-0" />
              <span>{approvalDemoLabel(decision)}</span>
            </div>
            <ManualDemoRetryBar onRetry={() => setDecision(null)} />
          </>
        ) : (
          <ApprovalCard
            approval={DEMO_APPROVAL}
            onDecide={(next) => setDecision(next)}
          />
        )}
      </div>
    </TooltipProvider>
  );
}
