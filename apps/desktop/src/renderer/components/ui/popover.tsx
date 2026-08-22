import { cn } from "@/lib/utils";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import type { ComponentProps } from "react";

/**
 * Popover primitives over Radix — portal rendering (escapes the composer's
 * overflow / z-index), anchor-relative positioning with collision flipping, and
 * outside-click / Esc dismissal come for free. Unlike Dialog this is non-modal
 * (no focus trap / scroll lock), so the anchor's own input can keep focus while
 * the popover is open — exactly what the @-mention menu needs (type-ahead while
 * a listbox floats above). Position via `<PopoverAnchor>`; match the anchor
 * width with `w-[var(--radix-popover-trigger-width)]`.
 */
export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;

export function PopoverContent({
  className,
  align = "center",
  sideOffset = 6,
  ...props
}: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-overlay outline-none",
          "data-[state=open]:animate-dropdown-in",
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}
