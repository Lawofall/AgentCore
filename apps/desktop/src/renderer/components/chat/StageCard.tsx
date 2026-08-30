import { cn } from "@/lib/utils";
import type { InteractionEntry } from "@/stores/interactions";

/**
 * 阶段推进卡已不是开辩入口。pending 不再可点；resolved / orphaned 只留墓碑。
 */
const shellClass =
  "rounded-xl border border-border bg-card p-3 text-sm text-foreground";

export function StageCard({ entry }: { entry: InteractionEntry }) {
  if (entry.status === "resolved") {
    const decision = String(entry.resolution?.decision ?? "");
    return (
      <div className={cn(shellClass, "opacity-70")} data-testid="stage-card">
        <div className="font-semibold">
          {decision === "research_first" ? "已选择先补充调研" : "已按此开辩"}
        </div>
      </div>
    );
  }
  if (entry.status === "orphaned") {
    return (
      <div className={cn(shellClass, "opacity-70")} data-testid="stage-card">
        <div className="font-semibold">阶段推进卡已失效</div>
        <p className="mt-1 text-xs text-muted-foreground">
          你已继续对话，此开辩入口不再可用。
        </p>
      </div>
    );
  }
  return (
    <div className={cn(shellClass, "opacity-70")} data-testid="stage-card">
      <div className="font-semibold">此开辩入口已下线</div>
      <p className="mt-1 text-xs text-muted-foreground">
        要辩，请直接在对话里说开辩。
      </p>
    </div>
  );
}
