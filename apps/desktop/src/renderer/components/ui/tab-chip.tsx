import { Button } from "@/components/ui/button";
import { IconButton } from "@/components/ui/icon-button";
import { NO_TAB_DRAG_ATTR } from "@/components/ui/tab-reorder";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { PictureInPicture2, X } from "lucide-react";
import {
  type HTMLAttributes,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  forwardRef,
} from "react";

export type TabChipVariant = "pill" | "strip";

export interface TabChipProps extends HTMLAttributes<HTMLDivElement> {
  /** `pill` = rounded dock / browser tabs; `strip` = file DetailTabs border-r chrome. */
  variant?: TabChipVariant;
  active?: boolean;
  icon?: ReactNode;
  label: string;
  /** Activate the tab (click on pill; pointer-down / Enter / Space on strip). */
  onSelect: () => void;
  onClose?: () => void;
  /** Omit on narrow / native — callers decide; this primitive does not hide it. */
  onPopOut?: () => void;
}

const overlayHiddenClass = "opacity-0 pointer-events-none";
const overlayRevealClass =
  "group-hover/tab:opacity-100 group-hover/tab:pointer-events-auto group-focus-within/tab:opacity-100 group-focus-within/tab:pointer-events-auto";

const actionFaceClass =
  "size-5 bg-inherit transition-opacity duration-fast motion-reduce:transition-none";

/**
 * Content-sized editor tab (VS Code chrome): idle width is icon + title.
 * Close / pop-out overlay the trailing edge (`position: absolute`) and stay in
 * the DOM when hidden (`opacity-0 pointer-events-none`).
 * Active + closable: `pr-6` gutter so the always-visible × sits beside the
 * title instead of covering the last character.
 */
export const TabChip = forwardRef<HTMLDivElement, TabChipProps>(
  function TabChip(
    {
      variant = "pill",
      active = false,
      icon,
      label,
      title,
      onSelect,
      onClose,
      onPopOut,
      className,
      onPointerDown,
      onKeyDown,
      ...rest
    },
    ref,
  ) {
    const closeLabel = `关闭 ${label}`;
    const popOutLabel = `弹出 ${label}`;
    const pill = variant === "pill";
    const closeRight = "right-1";
    const popOutRight = onClose ? "right-6" : "right-1";

    const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
      if (!pill) {
        if (event.button === 1 && onClose) {
          event.preventDefault();
          onClose();
        } else if (event.button === 0) {
          onSelect();
        }
      }
      onPointerDown?.(event);
    };

    const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
      if (!pill) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        } else if (
          (event.key === "Delete" || event.key === "Backspace") &&
          onClose
        ) {
          event.preventDefault();
          onClose();
        }
      }
      onKeyDown?.(event);
    };

    const stopActionPointer = (event: PointerEvent<HTMLButtonElement>) => {
      event.stopPropagation();
    };

    return (
      <div
        {...rest}
        ref={ref}
        title={title}
        {...(!pill
          ? {
              role: "tab" as const,
              "aria-selected": active,
              tabIndex: rest.tabIndex ?? 0,
            }
          : null)}
        onPointerDown={handlePointerDown}
        onKeyDown={handleKeyDown}
        className={cn(
          "group/tab relative flex min-w-0 shrink-0 items-center",
          pill &&
            cn(
              "rounded-lg",
              active
                ? "bg-accent text-foreground"
                : "text-muted-foreground hover:bg-accent/50",
            ),
          !pill &&
            cn(
              "cursor-pointer gap-1.5 border-r px-3 py-1.5 text-sm",
              active
                ? "bg-background text-foreground"
                : "bg-muted/40 text-muted-foreground hover:bg-muted",
            ),
          // Close is `size-5` + `right-1` (24px). Reserve that only while × is
          // painted — hover overlay still covers, so idle chips don't jump.
          active && onClose && "pr-6",
          className,
        )}
      >
        {pill ? (
          <Button
            variant="ghost"
            onClick={onSelect}
            icon={icon}
            title={title}
            className="h-auto min-w-0 max-w-[140px] gap-1.5 overflow-hidden rounded-none px-2.5 py-1 text-sm font-normal"
          >
            <span className="min-w-0 truncate">{label}</span>
          </Button>
        ) : (
          <>
            {icon}
            <span className="min-w-0 truncate">{label}</span>
          </>
        )}

        {onPopOut ? (
          <SimpleTooltip label="弹出为浮窗">
            <IconButton
              {...{ [NO_TAB_DRAG_ATTR]: "" }}
              onPointerDown={stopActionPointer}
              onClick={(event) => {
                event.stopPropagation();
                onPopOut();
              }}
              aria-label={popOutLabel}
              className={cn(
                "absolute top-1/2 z-10 -translate-y-1/2",
                popOutRight,
                actionFaceClass,
                overlayHiddenClass,
                overlayRevealClass,
              )}
            >
              <PictureInPicture2 size={12} />
            </IconButton>
          </SimpleTooltip>
        ) : null}

        {onClose ? (
          <IconButton
            {...{ [NO_TAB_DRAG_ATTR]: "" }}
            onPointerDown={stopActionPointer}
            onClick={(event) => {
              event.stopPropagation();
              onClose();
            }}
            aria-label={closeLabel}
            className={cn(
              "absolute top-1/2 z-10 -translate-y-1/2",
              closeRight,
              actionFaceClass,
              overlayHiddenClass,
              overlayRevealClass,
              active && "opacity-100 pointer-events-auto",
            )}
          >
            <X size={12} />
          </IconButton>
        ) : null}
      </div>
    );
  },
);
