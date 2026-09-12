/**
 * Left 画布导航 — outline of scene elements (click to select, double-click to zoom).
 * Chrome only; the engine stays the source of the scene. No resource-library search,
 * no in-canvas AI chat (those are product vetoes on this board).
 */

import { IconButton } from "@/components/ui";
import { cn } from "@/lib/utils";
import {
  ChevronDown,
  ChevronRight,
  GripHorizontal,
  Layers,
  Search,
  X,
} from "lucide-react";
import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { elementOutlineLabel, groupOutline } from "./outline";
import type { ElementType, SceneElement } from "./types";

const MIN_WIDTH = 240;
const MAX_WIDTH = 400;
const MIN_HEIGHT = 200;
const DEFAULT_WIDTH = 280;
const DEFAULT_HEIGHT = 440;

export interface WhiteboardSidePanelProps {
  open: boolean;
  onClose: () => void;
  elements: readonly SceneElement[];
  selectedIds: readonly string[];
  onSelect: (id: string) => void;
  onFocus: (id: string) => void;
}

export function WhiteboardSidePanel({
  open,
  onClose,
  elements,
  selectedIds,
  onSelect,
  onFocus,
}: WhiteboardSidePanelProps) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [filter, setFilter] = useState("");
  const [collapsed, setCollapsed] = useState<
    Partial<Record<ElementType, boolean>>
  >({});
  const resizeRef = useRef<{
    startX: number;
    startY: number;
    startW: number;
    startH: number;
  } | null>(null);

  useEffect(() => {
    const onMove = (ev: PointerEvent) => {
      const start = resizeRef.current;
      if (!start) return;
      setWidth(
        Math.max(
          MIN_WIDTH,
          Math.min(MAX_WIDTH, start.startW + (ev.clientX - start.startX)),
        ),
      );
      setHeight(
        Math.max(
          MIN_HEIGHT,
          Math.min(
            window.innerHeight * 0.85,
            start.startH + (ev.clientY - start.startY),
          ),
        ),
      );
    };
    const onUp = () => {
      resizeRef.current = null;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, []);

  const onResizeStart = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      resizeRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        startW: width,
        startH: height,
      };
    },
    [width, height],
  );

  const groups = useMemo(
    () => groupOutline(elements, filter),
    [elements, filter],
  );
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const filteredEmpty = elements.length > 0 && groups.length === 0;

  if (!open) return null;

  return (
    <div
      className="absolute left-3 top-14 z-10 flex flex-col overflow-hidden rounded-xl border border-border bg-card/95 shadow-md backdrop-blur"
      style={{ width, height }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h3 className="text-sm font-semibold text-foreground">画布导航</h3>
        <IconButton
          aria-label="关闭画布导航"
          title="关闭 (Ctrl+\\)"
          onClick={onClose}
        >
          <X size={16} />
        </IconButton>
      </div>

      <div className="px-2.5 pb-1.5 pt-2">
        <div className="relative">
          <Search
            size={14}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="过滤元素..."
            aria-label="过滤元素"
            className="w-full rounded-lg border border-border bg-background py-1.5 pl-8 pr-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {elements.length === 0 || filteredEmpty ? (
          <div className="flex h-28 flex-col items-center justify-center text-muted-foreground">
            <Layers size={22} className="mb-1.5 opacity-40" />
            <p className="text-xs">
              {filteredEmpty ? "未找到匹配元素" : "画布为空"}
            </p>
          </div>
        ) : (
          groups.map((group) => {
            const isCollapsed = collapsed[group.type] === true;
            return (
              <div key={group.type} className="mb-1">
                <button
                  type="button"
                  onClick={() =>
                    setCollapsed((prev) => ({
                      ...prev,
                      [group.type]: !isCollapsed,
                    }))
                  }
                  className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  {isCollapsed ? (
                    <ChevronRight size={12} />
                  ) : (
                    <ChevronDown size={12} />
                  )}
                  <span>{group.label}</span>
                  <span className="font-normal">({group.items.length})</span>
                </button>
                {isCollapsed
                  ? null
                  : group.items.map((el) => {
                      const on = selected.has(el.id);
                      return (
                        <button
                          key={el.id}
                          type="button"
                          aria-pressed={on}
                          onClick={() => onSelect(el.id)}
                          onDoubleClick={() => onFocus(el.id)}
                          className={cn(
                            "flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-sm",
                            on
                              ? "bg-accent text-accent-foreground"
                              : "text-muted-foreground hover:bg-accent hover:text-foreground",
                          )}
                        >
                          <span className="min-w-0 flex-1 truncate">
                            {elementOutlineLabel(el)}
                          </span>
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {group.label}
                          </span>
                        </button>
                      );
                    })}
              </div>
            );
          })
        )}
      </div>

      <div className="flex items-center justify-end border-t border-border px-3 py-1">
        <button
          type="button"
          aria-label="拖拽调整大小"
          title="拖拽调整大小"
          onPointerDown={onResizeStart}
          className="cursor-se-resize p-0.5 text-muted-foreground hover:text-foreground"
        >
          <GripHorizontal size={16} />
        </button>
      </div>
    </div>
  );
}
