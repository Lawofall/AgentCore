/**
 * Right property dock — transform + style for the selection (AI协作白板.md).
 * Replaces the floating selection bars. No hyperlink / business-card sections.
 */

import { IconButton } from "@/components/ui";
import { cn } from "@/lib/utils";
import {
  ImagePlus,
  LayoutGrid,
  Lock,
  Move,
  Paintbrush,
  PanelRightClose,
  PanelRightOpen,
  Trash2,
  Unlock,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  AlignRow,
  OpacityRow,
  StrokeRow,
  StyleRow,
  TextAlignRow,
} from "./chromeWidgets";
import type { WhiteboardEngine } from "./engine";
import { isLinear } from "./geometry";
import { TYPE_LABELS, elementOutlineLabel } from "./outline";
import {
  DEFAULT_STROKE_WIDTH,
  type SceneElement,
  type StrokeStyle,
  type TextAlign,
} from "./types";

type PropertyTab = "transform" | "style";

export interface WhiteboardPropertyPanelProps {
  open: boolean;
  onClose: () => void;
  onOpen: () => void;
  selected: readonly SceneElement[];
  swatches: string[];
  style: {
    fill?: string;
    stroke?: string;
    strokeWidth?: number;
    strokeStyle?: StrokeStyle;
    textAlign?: TextAlign;
    opacity?: number;
  };
  onStyle: (patch: {
    fill?: string | null;
    stroke?: string | null;
    strokeWidth?: number;
    strokeStyle?: StrokeStyle;
    textAlign?: TextAlign;
    opacity?: number;
  }) => void;
  onPatchBox: (patch: {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    rotation?: number;
  }) => void;
  onLock: (locked: boolean) => void;
  onDelete: () => void;
  onExportPng: () => void;
  onGridLayout: () => void;
  run: (fn: (e: WhiteboardEngine) => void) => void;
}

export function WhiteboardPropertyPanel({
  open,
  onClose,
  onOpen,
  selected,
  swatches,
  style,
  onStyle,
  onPatchBox,
  onLock,
  onDelete,
  onExportPng,
  onGridLayout,
  run,
}: WhiteboardPropertyPanelProps) {
  const count = selected.length;
  const [tab, setTab] = useState<PropertyTab>("transform");
  const only = count === 1 ? selected[0] : null;
  const skipTransform =
    !!only && (isLinear(only.type) || only.type === "freedraw");

  useEffect(() => {
    if (skipTransform) setTab("style");
  }, [skipTransform]);

  if (count === 0) return null;

  if (!open) {
    return (
      <button
        type="button"
        aria-label="打开属性面板"
        title="属性 (Ctrl+/)"
        onClick={onOpen}
        onPointerDown={(e) => e.stopPropagation()}
        className="pointer-events-auto absolute right-0 top-1/2 z-10 -translate-y-1/2 rounded-l-lg border border-r-0 border-border bg-card/95 p-1.5 text-muted-foreground shadow-sm hover:bg-accent hover:text-foreground"
      >
        <PanelRightOpen size={16} />
      </button>
    );
  }

  const effectiveTab = skipTransform ? "style" : tab;
  const title = only ? elementOutlineLabel(only) : `多选 (${count})`;
  const subtitle = only ? TYPE_LABELS[only.type] : "批量编辑共有属性";

  return (
    <div
      className="absolute bottom-0 right-0 top-0 z-10 flex w-[280px] flex-col overflow-hidden border-l border-border bg-card/95 shadow-md backdrop-blur pointer-events-auto"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-foreground">
            {title}
          </h3>
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        </div>
        <IconButton
          aria-label="关闭属性面板"
          title="关闭 (Ctrl+/)"
          onClick={onClose}
        >
          <PanelRightClose size={16} />
        </IconButton>
      </div>

      {only && !skipTransform ? (
        <div className="flex border-b border-border">
          {(
            [
              ["transform", "变换", Move],
              ["style", "样式", Paintbrush],
            ] as const
          ).map(([id, label, Icon]) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1 py-2 text-xs font-medium transition-colors",
                effectiveTab === id
                  ? "border-b-2 border-primary text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon size={12} />
              {label}
            </button>
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {count >= 2 ? (
          <>
            <AlignRow canAlign canDistribute={count >= 3} run={run} />
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-foreground hover:bg-accent"
              onClick={onGridLayout}
            >
              <LayoutGrid size={14} />
              网格布局
            </button>
          </>
        ) : null}

        {only && effectiveTab === "transform" ? (
          <TransformFields el={only} onPatch={onPatchBox} />
        ) : null}

        {(count >= 2 || effectiveTab === "style") && (
          <div className="space-y-3">
            <StyleRow
              label="描边"
              swatches={swatches}
              active={style.stroke}
              onPick={(c) => onStyle({ stroke: c })}
            />
            <StyleRow
              label="填充"
              swatches={swatches}
              active={style.fill}
              onPick={(c) => onStyle({ fill: c })}
            />
            <StrokeRow
              activeWidth={style.strokeWidth ?? DEFAULT_STROKE_WIDTH}
              dashed={style.strokeStyle === "dashed"}
              onWidth={(w) => onStyle({ strokeWidth: w })}
              onStyle={(s) => onStyle({ strokeStyle: s })}
            />
            {only &&
            only.type !== "image" &&
            only.type !== "freedraw" &&
            !isLinear(only.type) ? (
              <TextAlignRow
                active={style.textAlign}
                onPick={(a) => onStyle({ textAlign: a })}
              />
            ) : null}
            <OpacityRow
              active={style.opacity ?? 1}
              onPick={(o) => onStyle({ opacity: o })}
            />
            <div className="flex gap-1">
              <IconButton
                aria-label="锁定"
                title="锁定"
                onClick={() => onLock(true)}
              >
                <Lock size={14} />
              </IconButton>
              <IconButton
                aria-label="解锁"
                title="解锁"
                onClick={() => onLock(false)}
              >
                <Unlock size={14} />
              </IconButton>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center justify-end gap-1 border-t border-border px-2 py-1.5">
        <IconButton
          aria-label="导出选区 PNG"
          title="导出选区 PNG"
          onClick={onExportPng}
        >
          <ImagePlus size={16} />
        </IconButton>
        <IconButton
          aria-label="删除所选"
          title="删除所选 (Delete)"
          onClick={onDelete}
        >
          <Trash2 size={16} />
        </IconButton>
      </div>
    </div>
  );
}

function TransformFields({
  el,
  onPatch,
}: {
  el: SceneElement;
  onPatch: WhiteboardPropertyPanelProps["onPatchBox"];
}) {
  const deg = Math.round(((el.rotation ?? 0) * 180) / Math.PI);
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Move size={12} />
          位置
        </div>
        <div className="grid grid-cols-2 gap-2">
          <NumField label="X" value={el.x} onCommit={(x) => onPatch({ x })} />
          <NumField label="Y" value={el.y} onCommit={(y) => onPatch({ y })} />
        </div>
      </div>
      <div>
        <div className="mb-2 text-xs font-medium text-muted-foreground">
          尺寸
        </div>
        <div className="grid grid-cols-2 gap-2">
          <NumField
            label="W"
            value={el.width}
            onCommit={(width) => onPatch({ width })}
          />
          <NumField
            label="H"
            value={el.height}
            onCommit={(height) => onPatch({ height })}
          />
        </div>
      </div>
      <div>
        <div className="mb-2 text-xs font-medium text-muted-foreground">
          旋转
        </div>
        <NumField
          label="°"
          value={deg}
          onCommit={(d) => onPatch({ rotation: (d * Math.PI) / 180 })}
        />
      </div>
    </div>
  );
}

function NumField({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: number;
  onCommit: (n: number) => void;
}) {
  const shown = String(Math.round(value * 10) / 10);
  const [draft, setDraft] = useState(shown);
  useEffect(() => {
    setDraft(shown);
  }, [shown]);
  const commit = () => {
    if (draft === shown) return;
    const n = Number(draft);
    if (Number.isFinite(n)) onCommit(n);
    else setDraft(shown);
  };
  return (
    <label className="flex items-center gap-2">
      <span className="w-3 text-xs text-muted-foreground">{label}</span>
      <input
        type="number"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
        className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground outline-none [appearance:textfield] focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
      />
    </label>
  );
}
