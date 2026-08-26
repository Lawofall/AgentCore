import { IconButton } from "@/components/ui/icon-button";
import { cn } from "@/lib/utils";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  type HTMLAttributes,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  NO_TAB_DRAG_ATTR,
  type ReorderAxis,
  type ReorderPlace,
  TAB_DRAG_THRESHOLD_PX,
  moveItem,
  placeAlongAxis,
} from "./tab-reorder";
import { useHorizontalTabScroll } from "./useHorizontalTabScroll";

export {
  moveItem,
  NO_TAB_DRAG_ATTR,
  placeAlongAxis,
  TAB_DRAG_THRESHOLD_PX,
  type ReorderAxis,
  type ReorderPlace,
} from "./tab-reorder";
export {
  useHorizontalTabScroll,
  type HorizontalTabScrollState,
} from "./useHorizontalTabScroll";

export interface HorizontalTabStripProps {
  children: ReactNode;
  className?: string;
  /** Classes on the inner flex row that holds tabs. */
  contentClassName?: string;
  /** When false, only fades are shown (no chevron buttons). Default true. */
  showOverflowButtons?: boolean;
  "aria-label"?: string;
}

/**
 * Horizontally scrollable tab row with overflow fades and optional ‹ › controls.
 * Pass tab cells as `children` (often via `SortableTab` / `useSortableTabIds`).
 */
export function HorizontalTabStrip({
  children,
  className,
  contentClassName,
  showOverflowButtons = true,
  "aria-label": ariaLabel = "标签页",
}: HorizontalTabStripProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const { canScrollLeft, canScrollRight, scrollByPage } =
    useHorizontalTabScroll(scrollRef, contentRef);
  const overflow = canScrollLeft || canScrollRight;

  return (
    <nav
      className={cn(
        "relative flex min-h-0 min-w-0 items-center gap-0.5 self-stretch overflow-hidden",
        className,
      )}
      aria-label={ariaLabel}
    >
      {showOverflowButtons && overflow ? (
        <IconButton
          size="sm"
          disabled={!canScrollLeft}
          aria-label="向左滚动标签"
          onClick={() => scrollByPage(-1)}
          className={cn(!canScrollLeft && "opacity-40")}
        >
          <ChevronLeft size={14} aria-hidden />
        </IconButton>
      ) : null}

      {/*
        min-h-0 on the flex chain: overflow-x scrollbar/min-content height must
        not inflate this box (flex default min-height:auto) or tabs sit high
        with empty space below inside h-* headers. `scrollbar-hidden` (globals.css)
        hides the native overlay bar (fade arrows cover overflow) and must stay a
        plain class — the Tailwind utility form loses to unlayered globals.
      */}
      <div className="relative flex min-h-0 min-w-0 flex-1 items-center self-stretch overflow-hidden">
        {canScrollLeft ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 left-0 z-10 w-6 bg-gradient-to-r from-card to-transparent"
          />
        ) : null}
        {canScrollRight ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 z-10 w-6 bg-gradient-to-l from-card to-transparent"
          />
        ) : null}
        <div
          ref={scrollRef}
          className="scrollbar-hidden min-h-0 w-full max-h-full overflow-x-auto overflow-y-hidden"
        >
          <div
            ref={contentRef}
            className={cn(
              "flex min-w-0 items-center gap-0.5",
              contentClassName,
            )}
          >
            {children}
          </div>
        </div>
      </div>

      {showOverflowButtons && overflow ? (
        <IconButton
          size="sm"
          disabled={!canScrollRight}
          aria-label="向右滚动标签"
          onClick={() => scrollByPage(1)}
          className={cn(!canScrollRight && "opacity-40")}
        >
          <ChevronRight size={14} aria-hidden />
        </IconButton>
      ) : null}
    </nav>
  );
}

type DragSession = {
  pointerId: number;
  fromId: string;
  startX: number;
  startY: number;
  dragging: boolean;
  overId: string | null;
  place: ReorderPlace;
  el: HTMLElement;
};

/** Lift geometry for a floating drag ghost (pointer-follow; not the source row). */
export type SortableDragPreview = {
  id: string;
  width: number;
  height: number;
  grabOffsetX: number;
  grabOffsetY: number;
  pointerX: number;
  pointerY: number;
};

export interface UseSortableTabIdsOptions {
  disabled?: boolean;
  thresholdPx?: number;
  /**
   * Hit-test axis for before/after. Default `"x"` keeps the dock tab strip
   * horizontal; stacked rows (sidebar folder groups) pass `"y"`.
   */
  axis?: ReorderAxis;
  /**
   * Idle grab cursor. Tabs default to grab; folder group rows pass `false`
   * so the row stays a clickable header until a drag actually starts.
   */
  idleGrabCursor?: boolean;
  /** Source-row classes while dragging. Tabs fade at 60%; stacked rows pass 40%. */
  draggingClassName?: string;
}

export interface SortableTabItemProps
  extends Omit<HTMLAttributes<HTMLElement>, "id"> {
  "data-tab-id": string;
  "data-dragging"?: string;
}

/**
 * Pointer-based tab reorder (capture after threshold; not HTML5 DnD).
 * Spread `getItemProps(id)` onto each tab root; mark close/etc with `data-no-tab-drag`.
 *
 * Lifecycle: idle → armed (document listeners, no capture) → dragging
 * (capture + suppress trailing click) → idle. Early capture would retarget
 * `click` to the sortable root and break child Button `onClick` activation.
 */
export function useSortableTabIds(
  ids: readonly string[],
  onReorder: (orderedIds: string[]) => void,
  options?: UseSortableTabIdsOptions,
) {
  const disabled = options?.disabled ?? false;
  const thresholdPx = options?.thresholdPx ?? TAB_DRAG_THRESHOLD_PX;
  const axis = options?.axis ?? "x";
  const idleGrabCursor = options?.idleGrabCursor ?? true;
  const draggingClassName =
    options?.draggingClassName ?? "cursor-grabbing opacity-60";
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [overTarget, setOverTarget] = useState<{
    overId: string;
    place: ReorderPlace;
  } | null>(null);
  const [dragPreview, setDragPreview] = useState<SortableDragPreview | null>(
    null,
  );
  const sessionRef = useRef<DragSession | null>(null);
  /** Survives past pointerup so the trailing click does not activate the tab. */
  const suppressClickRef = useRef(false);
  const detachDocListenersRef = useRef<(() => void) | null>(null);
  const idsRef = useRef(ids);
  idsRef.current = ids;
  const onReorderRef = useRef(onReorder);
  onReorderRef.current = onReorder;
  const thresholdPxRef = useRef(thresholdPx);
  thresholdPxRef.current = thresholdPx;
  const axisRef = useRef(axis);
  axisRef.current = axis;

  const detachDocListeners = useCallback(() => {
    detachDocListenersRef.current?.();
    detachDocListenersRef.current = null;
  }, []);

  const endSession = useCallback(
    (pointerId: number, commit: boolean) => {
      const session = sessionRef.current;
      if (!session || session.pointerId !== pointerId) return;
      const { el } = session;
      sessionRef.current = null;
      setDraggingId(null);
      setOverTarget(null);
      setDragPreview(null);
      detachDocListeners();
      try {
        if (el.hasPointerCapture?.(pointerId)) {
          el.releasePointerCapture(pointerId);
        }
      } catch {
        /* already released */
      }
      if (
        commit &&
        session.dragging &&
        session.overId &&
        session.overId !== session.fromId
      ) {
        const next = moveItem(
          idsRef.current,
          session.fromId,
          session.overId,
          session.place,
        );
        const same =
          next.length === idsRef.current.length &&
          next.every((id, i) => id === idsRef.current[i]);
        if (!same) onReorderRef.current(next);
      }
    },
    [detachDocListeners],
  );

  const resolveOver = useCallback(
    (clientX: number, clientY: number, fromId: string) => {
      const nodes = document.elementsFromPoint(clientX, clientY);
      for (const node of nodes) {
        if (!(node instanceof Element)) continue;
        const tab = node.closest("[data-tab-id]");
        if (!tab) continue;
        const overId = tab.getAttribute("data-tab-id");
        if (!overId || overId === fromId) continue;
        const rect = tab.getBoundingClientRect();
        const place = placeAlongAxis(axisRef.current, clientX, clientY, rect);
        return { overId, place };
      }
      return null;
    },
    [],
  );

  const attachDocListeners = useCallback(
    (el: HTMLElement, fromId: string, pointerId: number) => {
      detachDocListeners();

      const onMove = (e: PointerEvent) => {
        const session = sessionRef.current;
        if (
          !session ||
          session.pointerId !== e.pointerId ||
          session.fromId !== fromId
        ) {
          return;
        }
        if (!session.dragging) {
          const dist = Math.hypot(
            e.clientX - session.startX,
            e.clientY - session.startY,
          );
          if (dist < thresholdPxRef.current) return;
          session.dragging = true;
          suppressClickRef.current = true;
          setDraggingId(fromId);
          const rect = el.getBoundingClientRect();
          setDragPreview({
            id: fromId,
            width: rect.width,
            height: rect.height,
            grabOffsetX: e.clientX - rect.left,
            grabOffsetY: e.clientY - rect.top,
            pointerX: e.clientX,
            pointerY: e.clientY,
          });
          try {
            el.setPointerCapture?.(e.pointerId);
          } catch {
            /* capture unsupported / pointer already up */
          }
        }
        const hit = resolveOver(e.clientX, e.clientY, fromId);
        if (hit) {
          session.overId = hit.overId;
          session.place = hit.place;
          setOverTarget((prev) =>
            prev?.overId === hit.overId && prev.place === hit.place
              ? prev
              : hit,
          );
        }
      };

      const onUp = (e: PointerEvent) => {
        if (e.pointerId !== pointerId) return;
        endSession(pointerId, true);
      };

      const onCancel = (e: PointerEvent) => {
        if (e.pointerId !== pointerId) return;
        suppressClickRef.current = false;
        endSession(pointerId, false);
      };

      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
      document.addEventListener("pointercancel", onCancel);
      detachDocListenersRef.current = () => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("pointercancel", onCancel);
      };
    },
    [detachDocListeners, endSession, resolveOver],
  );

  useEffect(() => {
    return () => {
      const session = sessionRef.current;
      if (session) {
        endSession(session.pointerId, false);
      } else {
        detachDocListeners();
      }
    };
  }, [detachDocListeners, endSession]);

  const getItemProps = useCallback(
    (id: string): SortableTabItemProps => {
      const onPointerDown = (e: ReactPointerEvent<HTMLElement>) => {
        if (disabled || e.button !== 0) return;
        const target = e.target as Element | null;
        if (target?.closest?.(`[${NO_TAB_DRAG_ATTR}]`)) return;
        if (sessionRef.current) {
          endSession(sessionRef.current.pointerId, false);
        }
        suppressClickRef.current = false;
        const el = e.currentTarget;
        sessionRef.current = {
          pointerId: e.pointerId,
          fromId: id,
          startX: e.clientX,
          startY: e.clientY,
          dragging: false,
          overId: null,
          place: "after",
          el,
        };
        // Armed only — capture after threshold so child Button clicks still fire.
        attachDocListeners(el, id, e.pointerId);
      };

      const onClickCapture = (e: ReactMouseEvent<HTMLElement>) => {
        if (suppressClickRef.current) {
          e.preventDefault();
          e.stopPropagation();
          suppressClickRef.current = false;
        }
      };

      return {
        "data-tab-id": id,
        ...(draggingId === id ? { "data-dragging": "true" } : {}),
        onPointerDown,
        onClickCapture,
        className: cn(
          "touch-none select-none",
          draggingId === id
            ? draggingClassName
            : disabled || !idleGrabCursor
              ? undefined
              : "cursor-grab",
        ),
      };
    },
    [
      attachDocListeners,
      disabled,
      draggingClassName,
      draggingId,
      endSession,
      idleGrabCursor,
    ],
  );

  return {
    getItemProps,
    draggingId,
    overId: overTarget?.overId ?? null,
    place: overTarget?.place ?? null,
    dragPreview,
  };
}

export interface SortableTabProps extends HTMLAttributes<HTMLDivElement> {
  id: string;
  /** From `useSortableTabIds`. */
  getItemProps: (id: string) => SortableTabItemProps;
}

/** Thin wrapper that merges sortable pointer props onto a tab root. */
export function SortableTab({
  id,
  getItemProps,
  className,
  children,
  ...rest
}: SortableTabProps) {
  const item = getItemProps(id);
  const {
    className: itemClassName,
    onPointerDown,
    onClickCapture,
    ...itemRest
  } = item;
  return (
    <div
      {...rest}
      {...itemRest}
      onPointerDown={(e) => {
        onPointerDown?.(e);
        rest.onPointerDown?.(e);
      }}
      onClickCapture={(e) => {
        onClickCapture?.(e);
        rest.onClickCapture?.(e);
      }}
      className={cn(itemClassName, className)}
    >
      {children}
    </div>
  );
}
