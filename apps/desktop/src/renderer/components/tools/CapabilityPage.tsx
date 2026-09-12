import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { Capabilities } from "@/services/capabilities";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { useCapabilities } from "./useCapabilities";

/** Shared loading / error / ready states for 技能 / 工具. Shell owns the chrome. */
export function CapabilityPage({
  fill = false,
  children,
}: {
  /** 填满宿主高度（市场货架）。 */
  fill?: boolean;
  children: (data: Capabilities) => ReactNode;
}) {
  const { data, status, reload } = useCapabilities();

  if (status === "loading") {
    return (
      <div
        className={cn(
          "flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm",
          fill && "min-h-0 flex-1",
        )}
      >
        <Loader2 size={16} className="animate-spin" />
        加载中…
      </div>
    );
  }

  if (status === "error") {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-3 rounded-xl border border-border border-dashed py-16 text-center",
          fill && "min-h-0 flex-1",
        )}
      >
        <p className="text-muted-foreground text-sm">能力列表加载失败</p>
        <Button onClick={() => reload()}>重试</Button>
      </div>
    );
  }

  if (status === "ready" && data && fill) {
    return <div className="flex min-h-0 flex-1 flex-col">{children(data)}</div>;
  }
  if (status === "ready" && data) {
    return <>{children(data)}</>;
  }
  return null;
}
