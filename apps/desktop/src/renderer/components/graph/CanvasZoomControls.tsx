import { IconButton } from "@/components/ui";
import { Maximize, Minus, Plus } from "lucide-react";

/**
 * Shared zoom cluster for the 全屏放大态 collaboration graph (统一观感,
 * 协作图与双视图UX.md §六 两个入口：聊天内嵌 ⇄ 全屏放大). {@link import("./GraphView")}
 * (interactive) floats this vertical 放大 / 缩小 / 适应 pill bottom-left, stacked
 * under {@link CanvasPlaybackControls} when frames exist. The chat embed is not
 * interactive and does not show this cluster. Each GraphView wires its own
 * ReactFlow instance through the callbacks. (Fit moved here from GraphToolbar,
 * which now selects layout only — zoom + fit live together.)
 */
export function CanvasZoomControls({
  onZoomIn,
  onZoomOut,
  onFit,
  fitLabel = "适应画布",
}: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  /** Fit button tooltip — the 放大态 adds its「(F)」hotkey hint. */
  fitLabel?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-border bg-card/90 p-1 shadow-sm backdrop-blur">
      <IconButton onClick={onZoomIn} aria-label="放大" title="放大">
        <Plus size={14} />
      </IconButton>
      <IconButton onClick={onZoomOut} aria-label="缩小" title="缩小">
        <Minus size={14} />
      </IconButton>
      <IconButton onClick={onFit} aria-label="适应画布" title={fitLabel}>
        <Maximize size={14} />
      </IconButton>
    </div>
  );
}
