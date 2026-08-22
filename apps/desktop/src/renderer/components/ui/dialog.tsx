import { PreviewObstruct } from "@/components/ui/preview-obstruct";
import { cn } from "@/lib/utils";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ComponentProps } from "react";

/**
 * Modal dialog primitives over Radix — focus trap, scroll lock, Esc-to-close and
 * outside-click are handled for us; we only supply tokenised chrome. Tokens and
 * radii follow `desktop-layout.mdc` / `color-tokens.mdc` (dialogs = `rounded-xl`,
 * popover surface, `bg-overlay` scrim). Compose with `Dialog` + `DialogContent`
 * (+ optional `DialogHeader` / `DialogTitle` / `DialogFooter`).
 */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogPortal = DialogPrimitive.Portal;

export function DialogOverlay({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      className={cn("fixed inset-0 z-50 bg-overlay", className)}
      {...props}
    />
  );
}

interface DialogContentProps
  extends ComponentProps<typeof DialogPrimitive.Content> {
  /** Vertical placement: centered (default) or anchored near the top, the
   *  command-palette / quick-search style. */
  position?: "center" | "top";
  /** Render the built-in top-right close affordance. Turn off for surfaces that
   *  own their own close control (e.g. a search header). */
  showClose?: boolean;
}

export function DialogContent({
  className,
  children,
  position = "center",
  showClose = true,
  ...props
}: DialogContentProps) {
  return (
    <DialogPortal>
      {/* 模态弹层打开 → 让内嵌预览的原生视图让位隐藏（否则会盖住本弹层，命令面板同理）。 */}
      <PreviewObstruct />
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(
          "fixed left-1/2 z-50 w-full max-w-lg -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-modal",
          "data-[state=open]:animate-dropdown-in",
          position === "center" ? "top-1/2 -translate-y-1/2" : "top-[15vh]",
          className,
        )}
        {...props}
      >
        {children}
        {showClose && (
          <DialogPrimitive.Close
            aria-label="关闭"
            className="absolute right-3 top-3 flex size-7 items-center justify-center rounded-lg text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X size={15} />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  );
}

export function DialogHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      // Default pr-12 reserves room for DialogContent's top-right close;
      // override via className (e.g. pr-5) when showClose={false}.
      className={cn("flex flex-col gap-1 px-5 pb-3 pt-5 pr-12", className)}
      {...props}
    />
  );
}

export function DialogFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex flex-col-reverse gap-2 px-5 pb-5 pt-3 sm:flex-row sm:justify-end",
        className,
      )}
      {...props}
    />
  );
}

export function DialogTitle({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn("text-base font-semibold text-foreground", className)}
      {...props}
    />
  );
}

export function DialogDescription({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}
