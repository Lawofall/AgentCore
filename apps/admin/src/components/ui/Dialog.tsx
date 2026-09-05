import { cn } from "@/lib/utils";
import { X } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

type Size = "sm" | "md" | "lg";

const SIZES: Record<Size, string> = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-2xl",
};

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * The console's modal.
 *
 * Six pages each hand-rolled this overlay, and all six shared the same gaps: no
 * `role="dialog"`, Esc did nothing, focus stayed loose behind the panel, and the
 * page scrolled under the overlay. Fixing it once here fixes it everywhere.
 *
 * The panel is capped at 85dvh with only the body scrolling, so the footer actions
 * can't be pushed out of reach on a short window.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  /** Off for dialogs showing something unrecoverable (a one-time password). */
  dismissOnOverlay = true,
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: Size;
  dismissOnOverlay?: boolean;
  /** While a request is in flight: suppress every dismissal path. */
  busy?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const busyRef = useRef(busy);
  const onCloseRef = useRef(onClose);
  busyRef.current = busy;
  onCloseRef.current = onClose;

  // Refs keep Escape/overlay in sync with the current busy flag without
  // tearing down the capture listener between confirm → done.
  const requestClose = useCallback(() => {
    if (busyRef.current) return;
    onCloseRef.current();
  }, []);

  // Restore focus to whatever opened the dialog, then move focus inside it.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const first = panelRef.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panelRef.current)?.focus();
    return () => opener?.focus?.();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        requestClose();
        return;
      }
      if (e.key !== "Tab") return;
      const nodes = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [open, requestClose]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Mouse affordance only, hidden from assistive tech: exposing it as a second
          "关闭" control would announce two identical buttons, and Esc + the header's
          close button already cover the keyboard and screen-reader paths.
          biome-ignore lint/a11y/noStaticElementInteractions: see above */}
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-overlay"
        onMouseDown={dismissOnOverlay ? requestClose : undefined}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "relative flex max-h-[85dvh] w-full flex-col overflow-hidden rounded-xl border border-border bg-card shadow-lg outline-none",
          SIZES[size],
        )}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-border border-b px-5 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="text-sm font-semibold text-foreground">
              {title}
            </h2>
            {description && (
              <p id={descriptionId} className="mt-1 text-xs text-muted-foreground">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            aria-label="关闭"
            onClick={requestClose}
            disabled={busy}
            className="-mr-1 shrink-0 rounded p-1 text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
          >
            <X size={16} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex shrink-0 items-center justify-end gap-2 border-border border-t px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
