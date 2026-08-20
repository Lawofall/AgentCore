import { chord } from "@/lib/shortcuts";
import { cn } from "@/lib/utils";
import { Search } from "lucide-react";
import { SurfaceRowButton } from "./surface-row";
import { SimpleTooltip } from "./tooltip";

/** Fake input that opens the global command palette (Cmd/Ctrl+K). */
export function SearchTrigger({
  collapsed = false,
  onClick,
  className,
}: {
  collapsed?: boolean;
  onClick: () => void;
  className?: string;
}) {
  if (collapsed) {
    const button = (
      <SurfaceRowButton
        aria-label="搜索或运行命令"
        onClick={onClick}
        className={cn("h-8 justify-center px-0", className)}
      >
        <Search size={16} className="shrink-0" />
      </SurfaceRowButton>
    );
    return (
      <SimpleTooltip label="搜索或运行命令" side="right">
        {button}
      </SimpleTooltip>
    );
  }

  return (
    <SurfaceRowButton
      onClick={onClick}
      className={cn("h-8 font-normal text-sidebar-foreground/55", className)}
    >
      <Search size={16} className="shrink-0" />
      搜索或运行命令
      <kbd className="ml-auto shrink-0 text-xs text-sidebar-foreground/40">
        {chord("k")}
      </kbd>
    </SurfaceRowButton>
  );
}
