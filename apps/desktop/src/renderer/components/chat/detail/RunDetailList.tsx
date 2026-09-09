import {
  type TimelineNode,
  estimateTimelineNodeSize,
} from "@/lib/processTimeline";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import { type ReactNode, useCallback, useMemo } from "react";

const HEAD_ESTIMATE = 288;
const FOOT_ESTIMATE = 72;
const PANE_PAD = 16;

type DetailItem =
  | { kind: "head" }
  | { kind: "node"; index: number; key: string; node: TimelineNode }
  | { kind: "foot" };

/** Pin the live process row and the foot (Thinking…) — not `count-1`, which is the foot. */
export function extraStreamingIndexes(
  base: number[],
  lastNodeItemIndex: number,
  footIndex: number,
): number[] {
  return [lastNodeItemIndex, footIndex].filter(
    (i) => i >= 0 && !base.includes(i),
  );
}

/**
 * Run-detail document as one virtual list: inspector chrome, each process row,
 * then the foot. Same scroller as {@link RunDetailScroll}; task scrolls away
 * with the log. Do not window only the process suffix (that leaves a gap).
 */
export function RunDetailList({
  scrollParent,
  head,
  foot,
  nodes,
  nodeKeys,
  isStreaming,
  renderNode,
}: {
  scrollParent: HTMLElement | null;
  head: ReactNode;
  foot: ReactNode;
  nodes: TimelineNode[];
  nodeKeys: string[];
  isStreaming: boolean;
  renderNode: (node: TimelineNode, index: number) => ReactNode;
}) {
  const showFoot = foot != null;
  const items = useMemo<DetailItem[]>(() => {
    const list: DetailItem[] = [{ kind: "head" }];
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      if (!node) continue;
      list.push({
        kind: "node",
        index: i,
        key: nodeKeys[i] ?? String(i),
        node,
      });
    }
    if (showFoot) list.push({ kind: "foot" });
    return list;
  }, [nodes, nodeKeys, showFoot]);

  const lastNodeItemIndex = nodes.length > 0 ? nodes.length : -1;
  const footIndex = showFoot ? items.length - 1 : -1;
  const windowed = scrollParent != null;

  const estimateSize = useCallback(
    (index: number) => {
      const item = items[index];
      if (!item) return 36;
      if (item.kind === "head") return HEAD_ESTIMATE;
      if (item.kind === "foot") return FOOT_ESTIMATE;
      return estimateTimelineNodeSize(item.node);
    },
    [items],
  );

  const getItemKey = useCallback(
    (index: number) => {
      const item = items[index];
      if (!item) return String(index);
      if (item.kind === "head") return "head";
      if (item.kind === "foot") return "foot";
      return item.key;
    },
    [items],
  );

  const rangeExtractor = useCallback(
    (range: Parameters<typeof defaultRangeExtractor>[0]) => {
      const base = defaultRangeExtractor(range);
      if (!isStreaming) return base;
      const extra = extraStreamingIndexes(base, lastNodeItemIndex, footIndex);
      return extra.length ? [...base, ...extra] : base;
    },
    [footIndex, isStreaming, lastNodeItemIndex],
  );

  const virtualizer = useVirtualizer({
    count: windowed ? items.length : 0,
    getScrollElement: () => scrollParent,
    estimateSize,
    overscan: 6,
    enabled: windowed,
    getItemKey,
    paddingStart: PANE_PAD,
    paddingEnd: PANE_PAD,
    // 0 = not laid out yet. Keep the estimate so the list cannot collapse
    // and mount every row.
    measureElement: (element, _entry, instance) => {
      const measured = element.getBoundingClientRect().height;
      if (measured > 0) return measured;
      const index = Number(element.getAttribute("data-index"));
      return Number.isFinite(index)
        ? instance.options.estimateSize(index)
        : measured;
    },
    observeElementRect: (_instance, cb) => {
      if (!scrollParent) return;
      const notify = () => {
        cb({
          width: Math.max(scrollParent.clientWidth, 1),
          height: Math.max(scrollParent.clientHeight, 1),
        });
      };
      notify();
      if (typeof ResizeObserver === "undefined") return;
      const ro = new ResizeObserver(notify);
      ro.observe(scrollParent);
      return () => ro.disconnect();
    },
    rangeExtractor,
  });

  if (!windowed) {
    return (
      <div className="p-4">
        {head}
        {nodes.length > 0 ? (
          <div className="mb-4 space-y-2">
            {nodes.map((node, i) => (
              <div key={nodeKeys[i] ?? i}>{renderNode(node, i)}</div>
            ))}
          </div>
        ) : null}
        {foot}
      </div>
    );
  }

  return (
    <div
      className="relative w-full"
      style={{ height: virtualizer.getTotalSize() }}
    >
      {virtualizer.getVirtualItems().map((vi) => {
        const item = items[vi.index];
        if (!item) return null;
        const isNode = item.kind === "node";
        return (
          <div
            key={vi.key}
            data-index={vi.index}
            data-run-detail-item={item.kind}
            data-timeline-node={isNode ? "" : undefined}
            ref={virtualizer.measureElement}
            className={
              item.kind === "head"
                ? "absolute top-0 left-0 w-full px-4 pb-4"
                : item.kind === "foot"
                  ? "absolute top-0 left-0 w-full px-4"
                  : "absolute top-0 left-0 w-full px-4 pb-2"
            }
            style={{ transform: `translateY(${vi.start}px)` }}
          >
            {item.kind === "head"
              ? head
              : item.kind === "foot"
                ? foot
                : renderNode(item.node, item.index)}
          </div>
        );
      })}
    </div>
  );
}
