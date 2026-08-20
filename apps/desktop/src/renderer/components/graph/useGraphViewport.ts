/** Fit-to-width viewport, resize observers, and zoom helpers for GraphView.
 * Host contract (Provider / fit / overflow) → `graphHost.tsx`. */

import { fitWidthBox } from "@/lib/elk-layout";
import { isGraphTraceEnabled, traceGraphViewport } from "@/services/graphTrace";
import type { ReactFlowInstance } from "@xyflow/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "./constants";
import type { GraphFitMode } from "./graphHost";

export type { GraphFitMode };

interface UseGraphViewportOptions {
  fitMode: GraphFitMode;
  /** Structural ELK content bbox (fixed NODE_* footprint). */
  bbox: { width: number; height: number } | null;
  layoutReady: boolean;
  onMeasure?: (m: { height: number; overflowing: boolean }) => void;
}

/**
 * Camera: setViewport only when structure bbox first ready / bbox identity
 * changes / container size changes. Never servo on measure / originY drift.
 */
export function useGraphViewport({
  fitMode,
  bbox,
  layoutReady,
  onMeasure,
}: UseGraphViewportOptions) {
  const rfRef = useRef<ReactFlowInstance | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [colWidth, setColWidth] = useState(0);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  const viewportSettledRef = useRef(false);

  const bboxKey =
    bbox && layoutReady
      ? `${bbox.width.toFixed(2)}x${bbox.height.toFixed(2)}`
      : "";

  useEffect(() => {
    if (!layoutReady) {
      viewportSettledRef.current = false;
    }
  }, [layoutReady]);

  const onInit = useCallback((inst: ReactFlowInstance) => {
    rfRef.current = inst;
    setRfInstance(inst);
  }, []);

  const fitView = useCallback(() => {
    rfRef.current?.fitView({ padding: 0.2, duration: 300 });
  }, []);

  const centerNode = useCallback((id: string) => {
    const node = rfRef.current?.getNode(id);
    if (!node) return;
    const w = node.measured?.width ?? 210;
    const h = node.measured?.height ?? 64;
    rfRef.current?.setCenter(node.position.x + w / 2, node.position.y + h / 2, {
      zoom: 1.2,
      duration: 300,
    });
  }, []);

  useEffect(() => {
    if (fitMode !== "width") return;
    const el = containerRef.current;
    if (!el) return;
    setColWidth(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      const w = rect?.width ?? 0;
      if (w > 0) setColWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fitMode]);

  useEffect(() => {
    if (
      fitMode !== "width" ||
      !rfInstance ||
      !bbox ||
      colWidth <= 0 ||
      !layoutReady ||
      !bboxKey
    ) {
      return;
    }
    // Width embed drives host height via onMeasure — never read colHeight back
    // into zoom (that re-created measure→height→fit jitter). Zoom/box from
    // structural bbox × column width only (fitWidthBox already caps EMBED_MAX).
    const fit = fitWidthBox(bbox.width, bbox.height, colWidth);
    const zoom = fit.zoom;
    const renderedW = bbox.width * zoom;
    const renderedH = bbox.height * zoom;
    const x = Math.max(0, (colWidth - renderedW) / 2);
    const y = renderedH <= fit.height ? (fit.height - renderedH) / 2 : 0;
    const animate = viewportSettledRef.current && !prefersReducedMotion();
    rfInstance.setViewport(
      { x, y, zoom },
      animate ? { duration: 200 } : undefined,
    );
    viewportSettledRef.current = true;
    setOverflowing(fit.overflowing);
    if (isGraphTraceEnabled()) {
      traceGraphViewport({
        fitMode,
        overflowing: fit.overflowing,
        zoom,
        renderedH,
        fitH: fit.height,
        bboxH: bbox.height,
        colWidth,
      });
    }
    onMeasure?.({ height: fit.height, overflowing: fit.overflowing });
  }, [fitMode, rfInstance, bbox, bboxKey, colWidth, layoutReady, onMeasure]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "f" && e.key !== "F") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      fitView();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitView]);

  useEffect(() => {
    if (fitMode !== "view") return;
    const el = containerRef.current;
    if (!el) return;
    let lastWidth = Math.round(el.clientWidth);
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (!settled) {
        settled = true;
        lastWidth = w;
        return;
      }
      if (w <= 0 || w === lastWidth) return;
      lastWidth = w;
      clearTimeout(timer);
      timer = setTimeout(() => fitView(), 160);
    });
    ro.observe(el);
    return () => {
      clearTimeout(timer);
      ro.disconnect();
    };
  }, [fitMode, fitView]);

  return {
    containerRef,
    rfRef,
    overflowing,
    fitView,
    centerNode,
    onInit,
  };
}
