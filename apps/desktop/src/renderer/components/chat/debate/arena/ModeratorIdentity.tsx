import type { Execution } from "@/stores/execution";
import { Gavel } from "lucide-react";
import type { DebateModel } from "../model";

/**
 * 主持人身份符号（贯穿剧本主列）：法槌 + 「主持人」。
 * 模型徽章只挂记分牌，此处不重复。终审舞台用更大标题变体（「主持人终审」）。
 */
export function ModeratorIdentity({
  gavelSize = 13,
  className = "",
}: {
  gavelSize?: number;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-muted-foreground ${className}`}
    >
      <Gavel size={gavelSize} className="shrink-0" aria-hidden />
      <span className="font-medium text-foreground">主持人</span>
    </span>
  );
}

/** 记分牌用：`moderatorRunId` → `execution.runs` → `model`。直播态 id 为空 → null。 */
export function resolveModeratorModel(
  debate: Pick<DebateModel, "moderatorRunId">,
  execution: Pick<Execution, "runs">,
): string | null {
  if (!debate.moderatorRunId) return null;
  const run = execution.runs.find((r) => r.id === debate.moderatorRunId);
  return run?.model ?? null;
}
