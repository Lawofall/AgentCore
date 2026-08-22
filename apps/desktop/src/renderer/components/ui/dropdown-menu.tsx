import { PreviewObstruct } from "@/components/ui/preview-obstruct";
import { cn } from "@/lib/utils";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import type { ComponentProps } from "react";

/**
 * Dropdown-menu primitives over Radix — roving-focus keyboard navigation,
 * type-ahead, outside-click and Esc dismissal, and portal positioning come for
 * free; we only supply tokenised chrome (menus = `rounded-lg`, popover surface,
 * `focus`/highlight → `accent`). `DropdownMenuItem` fires `onSelect` and the menu
 * auto-closes, so callers don't manage open state by hand.
 */
export const DropdownMenu = DropdownMenuPrimitive.Root;
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
export const DropdownMenuGroup = DropdownMenuPrimitive.Group;

export function DropdownMenuContent({
  className,
  sideOffset = 6,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <>
      {/* 菜单打开 → 让内嵌预览的原生视图让位隐藏（否则会盖住本菜单）。单独入一个 Portal：
          MenuPortal 不做 Children.map，同一 Portal 塞多个子节点会落进同一个 asChild Slot 报错。 */}
      <DropdownMenuPrimitive.Portal>
        <PreviewObstruct />
      </DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          sideOffset={sideOffset}
          className={cn(
            "z-50 min-w-44 overflow-hidden rounded-lg border border-border bg-popover py-1 text-popover-foreground shadow-overlay",
            "data-[state=open]:animate-dropdown-in",
            className,
          )}
          {...props}
        />
      </DropdownMenuPrimitive.Portal>
    </>
  );
}

interface DropdownMenuItemProps
  extends ComponentProps<typeof DropdownMenuPrimitive.Item> {
  /** `danger` tints the row destructive (e.g. delete / logout). */
  variant?: "default" | "danger";
}

export function DropdownMenuItem({
  className,
  variant = "default",
  ...props
}: DropdownMenuItemProps) {
  return (
    <DropdownMenuPrimitive.Item
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

export function DropdownMenuLabel({
  className,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Label>) {
  return (
    <DropdownMenuPrimitive.Label
      className={cn(
        "px-3 pb-1 pt-1.5 text-xs font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({
  className,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      className={cn("my-1 h-px bg-border", className)}
      {...props}
    />
  );
}
