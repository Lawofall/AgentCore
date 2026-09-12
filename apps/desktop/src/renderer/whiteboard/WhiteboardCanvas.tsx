import { IconButton } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { notifyActionError } from "@/lib/toast";
import { useIsDark } from "@/lib/useIsDark";
import { cn } from "@/lib/utils";
import { saveBlob } from "@/services/workspaceHttp";
import {
  Circle,
  Diamond,
  Eraser,
  Frame,
  Hand,
  ImagePlus,
  Layers,
  Maximize,
  Minus,
  MousePointer2,
  MoveUpRight,
  PanelLeftOpen,
  Pencil,
  Redo2,
  Shapes,
  Square,
  StickyNote,
  Type,
  Undo2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  type CSSProperties,
  type ChangeEvent,
  type ReactNode,
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { TextEditOverlay } from "./TextEditOverlay";
import { WhiteboardPropertyPanel } from "./WhiteboardPropertyPanel";
import { WhiteboardSidePanel } from "./WhiteboardSidePanel";
import { AlignRow } from "./chromeWidgets";
import { readSwatches } from "./colors";
import { WhiteboardEngine } from "./engine";
import type { StylePatch } from "./selectionOps";
import type {
  SceneElement,
  StrokeStyle,
  TextAlign,
  TextEditSession,
  Tool,
  Viewport,
  WhiteboardApi,
} from "./types";

interface MenuState {
  x: number;
  y: number;
}

interface SelStyle {
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  strokeStyle?: StrokeStyle;
  textAlign?: TextAlign;
  opacity?: number;
}

export interface WhiteboardCanvasProps {
  initialElements: SceneElement[];
  initialViewport?: Viewport;
  /** Preselect these element ids after the scene loads — the offline preview
   * (`#/preview/whiteboard`) uses it to render a selected state (rotation handle);
   * the app never sets it (selection comes from pointer). */
  initialSelectedIds?: string[];
  /** Fired on every committed element mutation (host debounces + autosaves). */
  onChange: (elements: SceneElement[], viewport: Viewport) => void;
  className?: string;
}

const TOOLS: Array<{ tool: Tool; icon: typeof Square; label: string }> = [
  { tool: "select", icon: MousePointer2, label: "选择 (V)" },
  { tool: "hand", icon: Hand, label: "抓手 / 平移 (H / 空格)" },
  { tool: "rectangle", icon: Square, label: "矩形 (R)" },
  { tool: "ellipse", icon: Circle, label: "椭圆 (O)" },
  { tool: "diamond", icon: Diamond, label: "菱形 (D)" },
  { tool: "arrow", icon: MoveUpRight, label: "箭头 (A)" },
  { tool: "line", icon: Minus, label: "直线 (L)" },
  { tool: "sticky", icon: StickyNote, label: "便签 (S)" },
  { tool: "text", icon: Type, label: "文字 (T)" },
  { tool: "freedraw", icon: Pencil, label: "画笔 (P)" },
  { tool: "frame", icon: Frame, label: "区域框 (F)" },
  { tool: "eraser", icon: Eraser, label: "橡皮 (E)" },
];

const SHAPE_TOOLS = TOOLS.filter(
  (t) => t.tool !== "select" && t.tool !== "hand",
);

const toolOn = "bg-accent text-accent-foreground";

function Chrome({
  className,
  style,
  children,
}: {
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "pointer-events-auto flex items-center gap-0.5 rounded-xl border border-border bg-card/95 p-1 shadow-md backdrop-blur",
        className,
      )}
      style={style}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {children}
    </div>
  );
}

function ChromeRule() {
  return <div className="mx-0.5 h-5 w-px bg-border" />;
}

/** One row in the right-click context menu. */
function MenuItem({
  label,
  hint,
  disabled,
  onClick,
}: {
  label: string;
  hint?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center justify-between gap-6 rounded-lg px-2.5 py-1.5 text-left text-sm text-foreground hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
    >
      <span>{label}</span>
      {hint ? (
        <span className="text-xs text-muted-foreground">{hint}</span>
      ) : null}
    </button>
  );
}

/** Self-built whiteboard canvas (AI协作白板.md §六) — replaces the old Excalidraw embed.
 * Owns a {@link WhiteboardEngine}; exposes an imperative {@link WhiteboardApi} (read scene,
 * apply AI ops, undo/redo, zoom) to the host page via ref. */
export const WhiteboardCanvas = forwardRef<
  WhiteboardApi,
  WhiteboardCanvasProps
>(function WhiteboardCanvas(
  { initialElements, initialViewport, initialSelectedIds, onChange, className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const engineRef = useRef<WhiteboardEngine | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const taRef = useRef<HTMLTextAreaElement>(null);
  const flushEditRef = useRef<() => string | null>(() => null);
  flushEditRef.current = () => (taRef.current ? taRef.current.value : null);

  const [tool, setTool] = useState<Tool>("select");
  const [edit, setEdit] = useState<TextEditSession | null>(null);
  const [navOpen, setNavOpen] = useState(true);
  const [propsOpen, setPropsOpen] = useState(true);
  const [outline, setOutline] = useState<SceneElement[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [zoom, setZoom] = useState(1);
  const selectionCount = selectedIds.length;
  const [selStyle, setSelStyle] = useState<SelStyle>({});
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [swatches, setSwatches] = useState<string[]>(() => readSwatches());
  const dark = useIsDark();

  // Mount once; the host remounts (key={boardId}) to load a different board, so
  // initialElements/initialViewport are an init-only seed — re-running this effect would
  // recreate the engine and drop edits.
  // biome-ignore lint/correctness/useExhaustiveDependencies: init-only seed, see above.
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const engine = new WhiteboardEngine(canvas, {
      onChange: () => {
        const scene = engine.getScene();
        setOutline(scene);
        onChangeRef.current(scene, engine.getViewport());
      },
      onSelectionChange: (ids) => {
        setSelectedIds(ids);
        setSelStyle(engine.getSelectedStyle());
      },
      onToolChange: (t) => setTool(t),
      onViewportChange: (z) => setZoom(z),
      onContextMenu: (x, y) => setMenu({ x, y }),
      onEditingChange: (session) => setEdit(session),
      flushEdit: () => flushEditRef.current(),
    });
    engineRef.current = engine;
    engine.loadScene(initialElements, initialViewport);
    setOutline(engine.getScene());
    if (initialSelectedIds?.length) {
      engine.selectIds(initialSelectedIds);
      setSelectedIds(engine.getSelectedIds());
    }

    const ro = new ResizeObserver(() => {
      const rect = container.getBoundingClientRect();
      engine.resize(rect.width, rect.height, window.devicePixelRatio || 1);
    });
    ro.observe(container);
    const rect = container.getBoundingClientRect();
    engine.resize(rect.width, rect.height, window.devicePixelRatio || 1);

    return () => {
      ro.disconnect();
      engine.destroy();
      engineRef.current = null;
    };
  }, []);

  // `dark` is a trigger only: re-read the palette + swatches from the DOM when the theme
  // flips (the values aren't referenced in the body).
  // biome-ignore lint/correctness/useExhaustiveDependencies: theme-flip trigger, see above.
  useEffect(() => {
    engineRef.current?.setDark();
    setSwatches(readSwatches());
  }, [dark]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "\\") {
        e.preventDefault();
        setNavOpen((open) => !open);
      }
      if ((e.ctrlKey || e.metaKey) && e.key === "/") {
        e.preventDefault();
        setPropsOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close the context menu on Escape while it's open.
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  // 导出选区 PNG：引擎只负责出 blob（保持框架/IO 无关），落盘统一走 saveBlob 下载
  // 接缝（桌面 = 主进程另存为；web = anchor）。
  const exportSelectionPng = useCallback(() => {
    const blob = engineRef.current?.selectionPngBlob();
    if (!blob) return;
    void saveBlob(blob, "whiteboard-selection.png").catch((e) =>
      notifyActionError("导出选区失败", e),
    );
  }, []);

  useImperativeHandle(
    ref,
    (): WhiteboardApi => ({
      getScene: () => engineRef.current?.getScene() ?? [],
      getViewport: () =>
        engineRef.current?.getViewport() ?? { panX: 0, panY: 0, zoom: 1 },
      getSelectedIds: () => engineRef.current?.getSelectedIds() ?? [],
      getSelectionBounds: () => engineRef.current?.getSelectionBounds() ?? null,
      setOverlay: (elements) => engineRef.current?.setOverlay(elements),
      addElements: (elements) => engineRef.current?.addElements(elements),
      rasterizeElements: (ids) => {
        const engine = engineRef.current;
        if (!engine) throw new Error("画布尚未就绪");
        return engine.rasterizeElements(ids);
      },
      applyOps: (ops) => engineRef.current?.applyOps(ops) ?? { created: [] },
      undo: () => engineRef.current?.undo(),
      redo: () => engineRef.current?.redo(),
      deleteSelected: () => engineRef.current?.deleteSelected(),
      zoomIn: () => engineRef.current?.zoomIn(),
      zoomOut: () => engineRef.current?.zoomOut(),
      zoomToFit: () => engineRef.current?.zoomToFit(),
      zoomToSelection: () => engineRef.current?.zoomToSelection(),
      resetZoom: () => engineRef.current?.resetZoom(),
      exportSelectionPng,
    }),
    [exportSelectionPng],
  );

  const pick = useCallback((t: Tool) => engineRef.current?.setTool(t), []);
  const selectFromNav = useCallback((id: string) => {
    engineRef.current?.selectIds([id]);
  }, []);
  const focusFromNav = useCallback((id: string) => {
    const engine = engineRef.current;
    if (!engine) return;
    engine.selectIds([id]);
    engine.zoomToSelection();
  }, []);
  const shapeActive = SHAPE_TOOLS.some((t) => t.tool === tool);
  const selectedEls = outline.filter((e) => selectedIds.includes(e.id));

  const openImagePicker = useCallback(() => fileInputRef.current?.click(), []);
  const onImagesPicked = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files?.length) engineRef.current?.insertImageFiles(files);
    e.target.value = ""; // reset so picking the same file again still fires onChange
  }, []);

  const applyStylePatch = useCallback((patch: StylePatch) => {
    engineRef.current?.applyStyle(patch);
    setSelStyle(engineRef.current?.getSelectedStyle() ?? {});
  }, []);
  const runEngine = useCallback((fn: (e: WhiteboardEngine) => void) => {
    const engine = engineRef.current;
    if (engine) fn(engine);
  }, []);
  const runMenu = useCallback(
    (fn: (e: WhiteboardEngine) => void) => {
      runEngine(fn);
      setMenu(null);
    },
    [runEngine],
  );

  return (
    <div
      ref={containerRef}
      className={cn("relative h-full w-full overflow-hidden", className)}
    >
      <canvas ref={canvasRef} className="block touch-none" />

      {/* Hidden picker backing the toolbar「插入图片」button (paste / drop also work). */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={onImagesPicked}
      />

      <WhiteboardSidePanel
        open={navOpen}
        onClose={() => setNavOpen(false)}
        elements={outline}
        selectedIds={selectedIds}
        onSelect={selectFromNav}
        onFocus={focusFromNav}
      />
      {!navOpen ? (
        <button
          type="button"
          aria-label="打开画布导航"
          title="画布导航 (Ctrl+\\)"
          onClick={() => setNavOpen(true)}
          onPointerDown={(e) => e.stopPropagation()}
          className="absolute left-0 top-1/2 z-10 -translate-y-1/2 rounded-r-lg border border-l-0 border-border bg-card/95 p-1.5 text-muted-foreground shadow-sm hover:bg-accent hover:text-foreground"
        >
          <PanelLeftOpen size={16} />
        </button>
      ) : null}

      <WhiteboardPropertyPanel
        open={propsOpen}
        onClose={() => setPropsOpen(false)}
        onOpen={() => setPropsOpen(true)}
        selected={selectedEls}
        swatches={swatches}
        style={selStyle}
        onStyle={applyStylePatch}
        onPatchBox={(patch) => engineRef.current?.patchSelected(patch)}
        onLock={(locked) =>
          locked
            ? engineRef.current?.lockSelected()
            : engineRef.current?.unlockSelected()
        }
        onDelete={() => engineRef.current?.deleteSelected()}
        onExportPng={exportSelectionPng}
        onGridLayout={() => engineRef.current?.layoutSelectedGrid()}
        run={runEngine}
      />

      {/* Compact top-center bar: history + select + shapes menu + image. */}
      <Chrome className="absolute left-1/2 top-3 z-10 -translate-x-1/2">
        <IconButton
          aria-label="撤销"
          title="撤销 (Ctrl+Z)"
          onClick={() => engineRef.current?.undo()}
        >
          <Undo2 size={16} />
        </IconButton>
        <IconButton
          aria-label="重做"
          title="重做 (Ctrl+Shift+Z)"
          onClick={() => engineRef.current?.redo()}
        >
          <Redo2 size={16} />
        </IconButton>
        <ChromeRule />
        <IconButton
          aria-label="选择 (V)"
          title="选择 (V)"
          onClick={() => pick("select")}
          className={cn(tool === "select" && toolOn)}
        >
          <MousePointer2 size={16} />
        </IconButton>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton
              aria-label="形状"
              title="形状"
              className={cn(shapeActive && toolOn)}
            >
              <Shapes size={16} />
            </IconButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" className="min-w-40">
            {SHAPE_TOOLS.map(({ tool: t, icon: Icon, label }) => (
              <DropdownMenuItem
                key={t}
                onSelect={() => pick(t)}
                className={cn(tool === t && "bg-accent")}
              >
                <Icon size={14} />
                {label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <IconButton
          aria-label="插入图片"
          title="插入图片（也可粘贴 / 拖入）"
          onClick={openImagePicker}
        >
          <ImagePlus size={16} />
        </IconButton>
        <ChromeRule />
        <IconButton
          aria-label="画布导航"
          title="画布导航 (Ctrl+\\)"
          onClick={() => setNavOpen((open) => !open)}
          className={cn(navOpen && toolOn)}
        >
          <Layers size={16} />
        </IconButton>
      </Chrome>

      {/* Zoom + hand — bottom-right; shift left when the property dock is open. */}
      <Chrome
        className={cn(
          "absolute bottom-3 z-10",
          propsOpen && selectionCount > 0 ? "right-[292px]" : "right-3",
        )}
      >
        <IconButton
          aria-label="抓手 / 平移 (H / 空格)"
          title="抓手 / 平移 (H / 空格)"
          onClick={() => pick(tool === "hand" ? "select" : "hand")}
          className={cn(tool === "hand" && toolOn)}
        >
          <Hand size={16} />
        </IconButton>
        <ChromeRule />
        <IconButton
          aria-label="缩小"
          title="缩小"
          onClick={() => engineRef.current?.zoomOut()}
        >
          <ZoomOut size={16} />
        </IconButton>
        <button
          type="button"
          onClick={() => engineRef.current?.resetZoom()}
          className="min-w-12 rounded-lg px-1 py-1 text-center text-xs text-muted-foreground hover:bg-accent"
          title="重置为 100%"
        >
          {Math.round(zoom * 100)}%
        </button>
        <IconButton
          aria-label="放大"
          title="放大"
          onClick={() => engineRef.current?.zoomIn()}
        >
          <ZoomIn size={16} />
        </IconButton>
        <IconButton
          aria-label="适应内容"
          title="适应内容"
          onClick={() => engineRef.current?.zoomToFit()}
        >
          <Maximize size={16} />
        </IconButton>
        <IconButton
          aria-label="缩放至选区"
          title="缩放至选区 (Ctrl+2)"
          onClick={() => engineRef.current?.zoomToSelection()}
          disabled={selectionCount === 0}
        >
          <Frame size={16} />
        </IconButton>
      </Chrome>

      {edit && engineRef.current ? (
        <TextEditOverlay
          engine={engineRef.current}
          session={edit}
          textareaRef={taRef}
        />
      ) : null}

      {/* Right-click context menu */}
      {menu ? (
        <>
          <button
            type="button"
            aria-label="关闭菜单"
            className="fixed inset-0 z-20 cursor-default"
            onClick={() => setMenu(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenu(null);
            }}
          />
          <div
            className="absolute z-30 min-w-40 rounded-xl border border-border bg-card p-1 shadow-lg"
            style={{ left: menu.x, top: menu.y }}
          >
            <MenuItem
              label="复制"
              hint="Ctrl+C"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.copySelection())}
            />
            <MenuItem
              label="粘贴"
              hint="Ctrl+V"
              onClick={() => runMenu((e) => e.paste())}
            />
            <MenuItem
              label="再制"
              hint="Ctrl+D"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.duplicateSelected())}
            />
            <MenuItem
              label="删除"
              hint="Delete"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.deleteSelected())}
            />
            <div className="my-1 h-px bg-border" />
            <AlignRow
              canAlign={selectionCount >= 2}
              canDistribute={selectionCount >= 3}
              run={runMenu}
            />
            <div className="my-1 h-px bg-border" />
            <MenuItem
              label="置顶"
              hint="Ctrl+]"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.bringToFront())}
            />
            <MenuItem
              label="上移一层"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.bringForward())}
            />
            <MenuItem
              label="下移一层"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.sendBackward())}
            />
            <MenuItem
              label="置底"
              hint="Ctrl+["
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.sendToBack())}
            />
            <div className="my-1 h-px bg-border" />
            <MenuItem
              label="编组"
              hint="Ctrl+G"
              disabled={selectionCount < 2}
              onClick={() => runMenu((e) => e.groupSelected())}
            />
            <MenuItem
              label="取消编组"
              hint="Ctrl+Shift+G"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.ungroupSelected())}
            />
            <div className="my-1 h-px bg-border" />
            <MenuItem
              label="网格布局"
              disabled={selectionCount < 2}
              onClick={() => runMenu((e) => e.layoutSelectedGrid())}
            />
            <MenuItem
              label="链路布局"
              disabled={selectionCount < 2}
              onClick={() => runMenu((e) => e.layoutSelectedDagre())}
            />
            <MenuItem
              label="导出选区 PNG"
              disabled={selectionCount === 0}
              onClick={() => runMenu(() => exportSelectionPng())}
            />
            <MenuItem
              label="缩放至选区"
              hint="Ctrl+2"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.zoomToSelection())}
            />
            <div className="my-1 h-px bg-border" />
            <MenuItem
              label="锁定"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.lockSelected())}
            />
            <MenuItem
              label="解锁"
              disabled={selectionCount === 0}
              onClick={() => runMenu((e) => e.unlockSelected())}
            />
            <MenuItem
              label="解锁全部"
              onClick={() => runMenu((e) => e.unlockAllOnBoard())}
            />
          </div>
        </>
      ) : null}
    </div>
  );
});
