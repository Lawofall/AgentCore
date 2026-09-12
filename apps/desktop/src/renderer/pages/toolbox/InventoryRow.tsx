import { IconButton, SurfaceRow, SurfaceRowActions } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { KeyboardEvent, ReactNode } from "react";

/**
 * 工具箱库存行（工作流）。SurfaceRow 家，不是第三套行：
 * 标题区点击进入编辑；启停等状态控件常显；其余动作悬停 / 焦点才进流。
 */
export function InventoryRow({
  title,
  badges,
  meta,
  detail,
  trailing,
  actions,
  onOpen,
  muted,
  className,
}: {
  title: ReactNode;
  badges?: ReactNode;
  meta?: ReactNode;
  detail?: ReactNode;
  trailing?: ReactNode;
  actions?: ReactNode;
  onOpen?: () => void;
  muted?: boolean;
  className?: string;
}) {
  const openable = Boolean(onOpen);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!onOpen) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen();
    }
  };

  return (
    <SurfaceRow
      className={cn(
        "group min-h-12 items-start gap-3 px-3 py-2.5",
        muted && "opacity-70",
        className,
      )}
    >
      <div
        className={cn("min-w-0 flex-1", openable && "cursor-pointer")}
        role={openable ? "button" : undefined}
        tabIndex={openable ? 0 : undefined}
        onClick={onOpen}
        onKeyDown={openable ? onKeyDown : undefined}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="min-w-0 truncate text-sm font-medium text-foreground">
            {title}
          </span>
          {badges}
        </div>
        {meta ? (
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {meta}
          </div>
        ) : null}
        {detail}
      </div>
      {trailing ? (
        <div className="flex shrink-0 items-center self-center">{trailing}</div>
      ) : null}
      {actions ? (
        <SurfaceRowActions className="self-center">{actions}</SurfaceRowActions>
      ) : null}
    </SurfaceRow>
  );
}

export function InventoryRowAction({
  label,
  disabled,
  onClick,
  destructive,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  destructive?: boolean;
  children: ReactNode;
}) {
  return (
    <SimpleTooltip label={label}>
      <IconButton
        size="sm"
        aria-label={label}
        disabled={disabled}
        className={
          destructive
            ? "hover:bg-destructive/10 hover:text-destructive"
            : undefined
        }
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
      >
        {children}
      </IconButton>
    </SimpleTooltip>
  );
}
