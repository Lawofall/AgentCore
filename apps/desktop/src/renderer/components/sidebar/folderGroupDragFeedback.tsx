import type { SortableDragPreview } from "@/components/ui/horizontal-tab-strip";
import type { ReorderPlace } from "@/components/ui/tab-reorder";
import { cn } from "@/lib/utils";
import { Cloud, HardDrive } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";

/** Drop-gap line on the hovered group header (VS Code / Linear / Cursor). */
export function FolderGroupInsertLine({ place }: { place: ReorderPlace }) {
  return (
    <div
      data-testid="folder-group-insert"
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-x-0 z-20 h-0.5 rounded-full bg-primary",
        place === "before"
          ? "top-0 -translate-y-1/2"
          : "bottom-0 translate-y-1/2",
      )}
    />
  );
}

/**
 * Pointer-following lift card. Source row stays in place (dimmed); this clone
 * is the only thing that moves so hit-testing on the list stays stable.
 */
export function FolderGroupDragGhost({
  label,
  isLocal,
  preview,
}: {
  label: string;
  isLocal: boolean;
  preview: SortableDragPreview;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const Icon = isLocal ? HardDrive : Cloud;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const placeGhost = (x: number, y: number) => {
      el.style.transform = `translate(${x - preview.grabOffsetX}px, ${
        y - preview.grabOffsetY
      }px)`;
    };
    placeGhost(preview.pointerX, preview.pointerY);
    const onMove = (e: PointerEvent) => placeGhost(e.clientX, e.clientY);
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
  }, [
    preview.grabOffsetX,
    preview.grabOffsetY,
    preview.pointerX,
    preview.pointerY,
  ]);

  return createPortal(
    <div
      ref={ref}
      data-testid="folder-group-drag-ghost"
      aria-hidden
      className={cn(
        "pointer-events-none fixed left-0 top-0 z-50 flex h-8 items-center gap-2",
        "rounded-lg border border-sidebar-border bg-sidebar px-2 text-sm",
        "text-sidebar-foreground opacity-90 shadow-overlay",
      )}
      style={{
        width: preview.width,
        height: preview.height,
        willChange: "transform",
      }}
    >
      <Icon
        size={14}
        className="shrink-0 text-sidebar-foreground/40"
        aria-hidden
      />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </div>,
    document.body,
  );
}

export function folderGroupInsertPlace(
  folderId: string,
  draggingId: string | null,
  overId: string | null,
  place: ReorderPlace | null,
): ReorderPlace | null {
  if (!draggingId || !overId || !place) return null;
  if (overId !== folderId || overId === draggingId) return null;
  return place;
}
