import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Button, type ButtonVariant } from "./button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./dialog";

export type ConfirmTone = "default" | "danger";

const confirmVariant: Record<ConfirmTone, ButtonVariant> = {
  default: "primary",
  danger: "destructive",
};

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  /** What actually happens / what cannot be undone. */
  description?: ReactNode;
  /** Extra body between the description and the footer — a password field, a
   *  detail list, an inline error. Rendered inside the dialog's side padding. */
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** `danger` = irreversible / destructive; renders the red confirm button. */
  tone?: ConfirmTone;
  /** The action is in flight: spinner on confirm, both buttons disabled, and the
   *  dialog refuses to close (Esc / scrim / close affordance) until it settles. */
  busy?: boolean;
  /** Gate the confirm button on body input (e.g. an empty password field). */
  confirmDisabled?: boolean;
  /** Fire-and-forget; wrap async work yourself (`() => void run()`) so this
   *  dialog never owns the pending state — `busy` is the caller's to drive. */
  onConfirm: () => void;
  className?: string;
}

/**
 * Yes/no modal for destructive or otherwise irreversible actions — the
 * in-product replacement for `window.confirm`, which is unstyled, blocks the
 * whole renderer, cannot carry a busy state, and is invisible to jsdom tests.
 *
 * Built on the shared {@link Dialog} primitives, so focus trap, scroll lock,
 * Esc-to-close and outside-click come from Radix.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel = "确定",
  cancelLabel = "取消",
  tone = "default",
  busy = false,
  confirmDisabled = false,
  onConfirm,
  className,
}: ConfirmDialogProps) {
  const requestClose = (next: boolean) => {
    if (busy) return;
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={requestClose}>
      <DialogContent
        showClose={!busy}
        className={cn("max-w-md", className)}
        onEscapeKeyDown={(e) => {
          if (busy) e.preventDefault();
        }}
        onInteractOutside={(e) => {
          if (busy) e.preventDefault();
        }}
      >
        <DialogHeader className={busy ? "pr-5" : undefined}>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children && <div className="px-5">{children}</div>}
        <DialogFooter>
          <Button
            variant="outline"
            className="h-9 px-4"
            disabled={busy}
            onClick={() => requestClose(false)}
          >
            {cancelLabel}
          </Button>
          <Button
            variant={confirmVariant[tone]}
            className="h-9 px-4"
            disabled={busy || confirmDisabled}
            icon={
              busy ? <Loader2 size={14} className="animate-spin" /> : undefined
            }
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
