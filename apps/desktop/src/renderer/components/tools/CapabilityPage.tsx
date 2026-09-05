import { PageContainer } from "@/components/layout/PageContainer";
import { Button, PageHeader } from "@/components/ui";
import { cn } from "@/lib/utils";
import { TOOLBOX_PAGE_BACK } from "@/pages/toolbox/manual/paths";
import type { Capabilities } from "@/services/capabilities";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { useCapabilities } from "./useCapabilities";

/** Shared shell for the 能力 sub-pages (工具 / AI 提示词): toolbox page header
 * and the loading / error / ready states around the shared capability fetch. */
export function CapabilityPage({
  title,
  fill = false,
  children,
}: {
  title: string;
  /** 填满宿主高度（AI 提示词阅读器）；工具页保持整页滚动。 */
  fill?: boolean;
  children: (data: Capabilities) => ReactNode;
}) {
  const { data, status, reload } = useCapabilities();

  return (
    <PageContainer width="canvas" fill={fill}>
      <PageHeader
        title={title}
        back={TOOLBOX_PAGE_BACK}
        className={fill ? "shrink-0" : undefined}
      />

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
