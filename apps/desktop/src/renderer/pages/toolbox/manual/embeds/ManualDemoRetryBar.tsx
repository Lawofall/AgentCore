import { Button } from "@/components/ui";

/** 手册演示闭环：结算记录下方的回退条。 */
export function ManualDemoRetryBar({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <p className="text-xs text-muted-foreground">演示，不会发给团队</p>
      <Button variant="ghost" size="sm" onClick={onRetry}>
        再试一次
      </Button>
    </div>
  );
}
