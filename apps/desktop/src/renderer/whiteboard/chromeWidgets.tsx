/**
 * Shared chrome widgets for the whiteboard: style swatches, stroke, text align, opacity,
 * and the multi-select align/distribute row. Used by the right property panel and the
 * right-click menu.
 */

import { cn } from "@/lib/utils";
import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignHorizontalDistributeCenter,
  AlignLeft,
  AlignRight,
  AlignStartHorizontal,
  AlignStartVertical,
  AlignVerticalDistributeCenter,
  Ban,
  type Square,
} from "lucide-react";
import type { WhiteboardEngine } from "./engine";
import type { AlignEdge } from "./selectionOps";
import { STROKE_WIDTHS, type StrokeStyle, type TextAlign } from "./types";

/** One labeled row of color swatches (描边 / 填充). */
export function StyleRow({
  label,
  swatches,
  active,
  onPick,
}: {
  label: string;
  swatches: string[];
  active?: string;
  onPick: (c: string | null) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="px-0.5 text-xs text-muted-foreground">{label}</span>
      {swatches.map((c) => (
        <button
          key={c}
          type="button"
          aria-label={`${label} ${c}`}
          title={label}
          onClick={() => onPick(c)}
          // Content color (token-derived oklch from --agent-*) — applied inline per
          // color-tokens.mdc's content-color carve-out, not a hardcoded chrome color.
          style={{ backgroundColor: c }}
          className={cn(
            "size-5 rounded-full border border-border/60 transition",
            active === c &&
              "ring-2 ring-primary ring-offset-1 ring-offset-card",
          )}
        />
      ))}
      <button
        type="button"
        aria-label={`清除${label}`}
        title={`清除${label}（恢复默认）`}
        onClick={() => onPick(null)}
        className={cn(
          "flex size-5 items-center justify-center rounded-full border border-border text-muted-foreground hover:bg-accent",
          active === undefined &&
            "ring-2 ring-primary ring-offset-1 ring-offset-card",
        )}
      >
        <Ban size={12} />
      </button>
    </div>
  );
}

const WIDTH_LABELS = ["细", "中", "粗"];

export function StrokeRow({
  activeWidth,
  dashed,
  onWidth,
  onStyle,
}: {
  activeWidth: number;
  dashed: boolean;
  onWidth: (w: number) => void;
  onStyle: (s: StrokeStyle) => void;
}) {
  const cell =
    "flex size-5 items-center justify-center rounded-full border border-border/60 text-foreground transition hover:bg-accent";
  const ring = "ring-2 ring-primary ring-offset-1 ring-offset-card";
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="px-0.5 text-xs text-muted-foreground">线宽</span>
      {STROKE_WIDTHS.map((w, i) => (
        <button
          key={w}
          type="button"
          aria-label={`线宽 ${WIDTH_LABELS[i]}`}
          title={WIDTH_LABELS[i]}
          onClick={() => onWidth(w)}
          className={cn(cell, activeWidth === w && ring)}
        >
          <span
            className="block rounded-full bg-current"
            style={{ width: 12, height: Math.min(6, w) }}
          />
        </button>
      ))}
      <div className="h-5 w-px bg-border" />
      <span className="px-0.5 text-xs text-muted-foreground">线型</span>
      <button
        type="button"
        aria-label="实线"
        title="实线"
        onClick={() => onStyle("solid")}
        className={cn(cell, !dashed && ring)}
      >
        <span className="block w-3.5 border-current border-t-2 border-solid" />
      </button>
      <button
        type="button"
        aria-label="虚线"
        title="虚线"
        onClick={() => onStyle("dashed")}
        className={cn(cell, dashed && ring)}
      >
        <span className="block w-3.5 border-current border-t-2 border-dashed" />
      </button>
    </div>
  );
}

export function TextAlignRow({
  active,
  onPick,
}: {
  active?: TextAlign;
  onPick: (a: TextAlign) => void;
}) {
  const cell =
    "flex size-5 items-center justify-center rounded-full border border-border/60 text-foreground transition hover:bg-accent";
  const ring = "ring-2 ring-primary ring-offset-1 ring-offset-card";
  const items: Array<{
    align: TextAlign;
    icon: typeof AlignLeft;
    label: string;
  }> = [
    { align: "left", icon: AlignLeft, label: "左对齐" },
    { align: "center", icon: AlignCenterHorizontal, label: "居中" },
    { align: "right", icon: AlignRight, label: "右对齐" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="px-0.5 text-xs text-muted-foreground">对齐</span>
      {items.map(({ align, icon: Icon, label }) => (
        <button
          key={align}
          type="button"
          aria-label={label}
          title={label}
          onClick={() => onPick(align)}
          className={cn(cell, active === align && ring)}
        >
          <Icon size={13} />
        </button>
      ))}
    </div>
  );
}

export function OpacityRow({
  active,
  onPick,
}: {
  active: number;
  onPick: (o: number) => void;
}) {
  const pct = Math.round(active * 100);
  return (
    <div className="flex items-center gap-1.5">
      <span className="px-0.5 text-xs text-muted-foreground">透明度</span>
      <input
        type="range"
        min={10}
        max={100}
        value={pct}
        onChange={(e) => onPick(Number(e.target.value) / 100)}
        className="h-1 w-20 accent-primary"
        aria-label="透明度"
      />
      <span className="w-8 text-xs text-muted-foreground">{pct}%</span>
    </div>
  );
}

export const ALIGN_ACTIONS: Array<{
  icon: typeof Square;
  label: string;
  edge: AlignEdge;
}> = [
  { icon: AlignStartVertical, label: "左对齐", edge: "left" },
  { icon: AlignCenterVertical, label: "水平居中", edge: "centerX" },
  { icon: AlignEndVertical, label: "右对齐", edge: "right" },
  { icon: AlignStartHorizontal, label: "顶对齐", edge: "top" },
  { icon: AlignCenterHorizontal, label: "垂直居中", edge: "centerY" },
  { icon: AlignEndHorizontal, label: "底对齐", edge: "bottom" },
];

export function AlignRow({
  canAlign,
  canDistribute,
  run,
}: {
  canAlign: boolean;
  canDistribute: boolean;
  run: (fn: (e: WhiteboardEngine) => void) => void;
}) {
  const btn =
    "flex size-7 items-center justify-center rounded-lg text-foreground hover:bg-accent disabled:pointer-events-none disabled:opacity-40";
  return (
    <div className="flex flex-wrap items-center gap-0.5 px-1 py-1">
      {ALIGN_ACTIONS.map(({ icon: Icon, label, edge }) => (
        <button
          key={edge}
          type="button"
          title={label}
          aria-label={label}
          disabled={!canAlign}
          onClick={() => run((e) => e.alignSelected(edge))}
          className={btn}
        >
          <Icon size={15} />
        </button>
      ))}
      <div className="mx-0.5 h-5 w-px bg-border" />
      <button
        type="button"
        title="水平分布"
        aria-label="水平分布"
        disabled={!canDistribute}
        onClick={() => run((e) => e.distributeSelected("x"))}
        className={btn}
      >
        <AlignHorizontalDistributeCenter size={15} />
      </button>
      <button
        type="button"
        title="垂直分布"
        aria-label="垂直分布"
        disabled={!canDistribute}
        onClick={() => run((e) => e.distributeSelected("y"))}
        className={btn}
      >
        <AlignVerticalDistributeCenter size={15} />
      </button>
    </div>
  );
}
