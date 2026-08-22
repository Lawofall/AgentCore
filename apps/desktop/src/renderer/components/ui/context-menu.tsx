import { PreviewObstruct } from "@/components/ui/preview-obstruct";
import { cn } from "@/lib/utils";
import * as ContextMenuPrimitive from "@radix-ui/react-context-menu";
import type { ComponentProps } from "react";

/**
 * Right-click context-menu primitives over Radix — pointer positioning, roving
 * focus, type-ahead, outside-click and Esc dismissal are handled for us; we only
 * supply tokenised chrome (matches `dropdown-menu.tsx` so menus look identical
 * regardless of how they're triggered). Wrap the right-clickable surface with
 * `ContextMenuTrigger` (`asChild`) and put items in `ContextMenuContent`.
 */
export const ContextMenu = ContextMenuPrimitive.Root;
export const ContextMenuTrigger = ContextMenuPrimitive.Trigger;
export const ContextMenuGroup = ContextMenuPrimitive.Group;

export function ContextMenuContent({
  className,
  ...props
}: ComponentProps<typeof ContextMenuPrimitive.Content>) {
  return (
    <>
      {/* 右键菜单打开 → 让内嵌预览的原生视图让位隐藏（否则会盖住本菜单）。单独入一个 Portal：
          MenuPortal 不做 Children.map，同一 Portal 塞多个子节点会落进同一个 asChild Slot 报错。 */}
      <ContextMenuPrimitive.Portal>
        <PreviewObstruct />
      </ContextMenuPrimitive.Portal>
      <ContextMenuPrimitive.Portal>
        <ContextMenuPrimitive.Content
          className={cn(
            "z-50 min-w-44 overflow-hidden rounded-lg border border-border bg-popover py-1 text-popover-foreground shadow-overlay",
            "data-[state=open]:animate-dropdown-in",
            className,
          )}
          {...props}
        />
      </ContextMenuPrimitive.Portal>
    </>
  );
}

interface ContextMenuItemProps
  extends ComponentProps<typeof ContextMenuPrimitive.Item> {
  /** `danger` tints the row destructive (e.g. delete). */
  variant?: "default" | "danger";
}

export function ContextMenuItem({
  className,
  variant = "default",
  ...props
}: ContextMenuItemProps) {
  return (
    <ContextMenuPrimitive.Item
      className={cn(
        "flex w-full cursor-default select-none items-center gap-2 px-3 py-1.5 text-left text-sm outline-none transition-colors",
        "focus:bg-accent data-[highlighted]:bg-accent",
        variant === "danger"
          ? "text-destructive focus:text-destructive data-[highlighted]:text-destructive"
          : "focus:text-accent-foreground data-[highlighted]:text-accent-foreground",
        "data-[disabled]:pointer-events-none data-[disabled]:text-muted-foreground/60",
        className,
      )}
      {...props}
    />
  );
}

export function ContextMenuLabel({
  className,
  ...props
}: ComponentProps<typeof ContextMenuPrimitive.Label>) {
  return (
    <ContextMenuPrimitive.Label
      className={cn(
        "px-3 pb-1 pt-1.5 text-xs font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function ContextMenuSeparator({
  className,
  ...props
}: ComponentProps<typeof ContextMenuPrimitive.Separator>) {
  return (
    <ContextMenuPrimitive.Separator
      className={cn("my-1 h-px bg-border", className)}
      {...props}
    />
  );
}
