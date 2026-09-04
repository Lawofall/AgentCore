import { PageContainer } from "@/components/layout/PageContainer";
import { ToolboxPageHeader } from "@/components/toolbox/ToolboxPageHeader";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { Capabilities } from "@/services/capabilities";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { useCapabilities } from "./useCapabilities";

/** Shared shell for the 能力 sub-pages (工具 / AI 提示词): the toolbox header
 * (back link + capability segments) and the loading / error / ready states around
 * the shared capability fetch. The page supplies a render function that gets the
 * loaded data. */
export function CapabilityPage({
  note,
  fill = false,
  children,
}: {
  /** 术语/范围说明，作为内容区第一行 muted 小字，不进页头。 */
  note?: ReactNode;
  /** 填满宿主高度（AI 提示词阅读器）；工具页保持整页滚动。 */
  fill?: boolean;
  children: (data: Capabilities) => ReactNode;
}) {
  const { data, status, reload } = useCapabilities();

  return (
    <PageContainer width="canvas" fill={fill}>
      <ToolboxPageHeader className={fill ? "shrink-0" : undefined} />

      {note ? (
        <p
          className={cn(
            "text-muted-foreground text-xs",
            fill ? "mb-3 shrink-0" : "mb-4",
          )}
        >
          {note}
        </p>
      ) : null}

      {status === "loading" && (
        <div
          className={cn(
            "flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm",
            fill && "min-h-0 flex-1",
          )}
        >
          <Loader2 size={16} className="animate-spin" />
          加载中…
        </div>
      )}
      {status === "error" && (
        <div
          className={cn(
            "flex flex-col items-center justify-center gap-3 rounded-xl border border-border border-dashed py-16 text-center",
            fill && "min-h-0 flex-1",
          )}
        >
          <p className="text-muted-foreground text-sm">能力列表加载失败</p>
          <Button onClick={() => reload()}>重试</Button>
        </div>
      )}
      {status === "ready" && data && fill ? (
        <div className="flex min-h-0 flex-1 flex-col">{children(data)}</div>
      ) : null}
      {status === "ready" && data && !fill ? children(data) : null}
    </PageContainer>
  );
}
