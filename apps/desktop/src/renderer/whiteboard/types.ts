/**
 * Self-built whiteboard engine — scene model (AI协作白板.md).
 *
 * Geometry is in WORLD coordinates; the {@link Viewport} maps world→screen at render
 * time. `SceneElement` is a pragmatic single interface keyed by `type` (the doc's
 * discriminated-union target — kept as one shape for the MVP skeleton so the renderer
 * and hit-test stay compact; tighten into a strict union as the shape set grows).
 *
 * `schemaVersion` is the per-element migration 后悔药: bump + migrate when an
 * element's fields change so an old persisted scene never silently misreads.
 */

export const SCENE_FORMAT = "agentcore-board";
export const SCENE_SCHEMA_VERSION = 1;

/** Outline thickness presets (world units) offered by the style panel; the renderer falls
 * back to {@link DEFAULT_STROKE_WIDTH} when an element has none. */
export const STROKE_WIDTHS = [2, 4, 7] as const;
export const DEFAULT_STROKE_WIDTH = 2;

export type StrokeStyle = "solid" | "dashed";
export type TextAlign = "left" | "center" | "right";

export type ElementType =
  | "rectangle"
  | "ellipse"
  | "diamond"
  | "sticky"
  | "text"
  | "freedraw"
  | "image"
  | "arrow"
  | "line"
  | "frame";

export interface SceneElement {
  id: string;
  type: ElementType;
  /** Top-left corner (world). For `freedraw`/`arrow` it is the points' bbox origin. */
  x: number;
  y: number;
  width: number;
  height: number;
  /** Label / text body (the `text` element uses it as its content). */
  text?: string;
  /** Explicit CSS color (e.g. AI-supplied); omit = theme-default per type at render. */
  fill?: string;
  stroke?: string;
  /** Outline thickness in world units; omit = renderer default ({@link DEFAULT_STROKE_WIDTH}). */
  strokeWidth?: number;
  /** Outline style; omit = solid. `dashed` strokes the shape (arrowheads stay solid). */
  strokeStyle?: StrokeStyle;
  fontSize?: number;
  /** freedraw: points RELATIVE to (x,y). arrow: absolute world points (used when unbound). */
  points?: Array<[number, number]>;
  /** Horizontal alignment of a `text` element's lines (omit = left). */
  textAlign?: TextAlign;
  /** image: the picture as a data URL (base64 PNG/JPEG), downscaled on import. Like 手绘,
   * its meaning lives in pixels → read via vision (board_read). */
  src?: string;
  /** Clockwise rotation in radians about the element's box center (omit = 0). Linear
   * elements (`arrow`/`line`) and `freedraw` are not rotated (their geometry is the points).
   * Exposed via the rotation handle above a single selection. */
  rotation?: number;
  /** Whole-element opacity 0..1 (omit = 1). */
  opacity?: number;
  /** Locked elements ignore pointer hit-testing / marquee / move / resize / delete until
   * unlocked (right-click →「解锁」or「解锁全部」). */
  locked?: boolean;
  /** arrow/line endpoint bindings (element ids) — endpoints computed from the bound elements. */
  start?: { id?: string };
  end?: { id?: string };
  groupIds?: string[];
  schemaVersion: number;
}

/** screen = world * zoom + pan (pan in CSS px). */
export interface Viewport {
  panX: number;
  panY: number;
  zoom: number;
}

/** In-flight text edit. `id` is the element being edited; `null` creates a new `text`
 * element at `world` on commit. The host React overlay holds the draft; the engine only
 * stores this session and applies {@link TextCommit} after a flush. */
export interface TextEditSession {
  id: string | null;
  world: [number, number];
}

/** What the host hands back when an edit ends. `text` is already trimmed. */
export interface TextCommit {
  id: string | null;
  world: [number, number];
  text: string;
}

/** The opaque scene blob we persist to `boards.scene` (our own format, not Excalidraw). */
export interface BoardScenePayload {
  format: string;
  schemaVersion: number;
  elements: SceneElement[];
  appState?: { viewport?: Viewport };
}

export type Tool =
  | "select"
  | "hand"
  | "rectangle"
  | "ellipse"
  | "diamond"
  | "sticky"
  | "text"
  | "freedraw"
  | "arrow"
  | "line"
  | "frame"
  | "eraser";

export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 8;

/** Imperative handle the host (WhiteboardCanvasPage) drives — the engine's public API. */
export interface WhiteboardApi {
  getScene(): SceneElement[];
  getViewport(): Viewport;
  getSelectedIds(): string[];
  /** Bounding box (world) of the current selection, or null if nothing is selected. */
  getSelectionBounds(): {
    x: number;
    y: number;
    width: number;
    height: number;
  } | null;
  /** Replace the transient overlay layer: drawn ON TOP of the scene, never serialized,
   * never in history, never hit-tested. Pass `[]` to clear. */
  setOverlay(elements: SceneElement[]): void;
  /** Append persistent elements as one history step. No-op for `[]`. */
  addElements(elements: SceneElement[]): void;
  /** Rasterize a subset of elements to a PNG for the AI's vision reader (board_read). */
  rasterizeElements(ids: string[]): { pngBase64: string; w: number; h: number };
  /** Apply a batch of AI board ops; returns the ids created this batch (in op order). */
  applyOps(ops: import("@/types/events").BoardOp[]): { created: string[] };
  undo(): void;
  redo(): void;
  deleteSelected(): void;
  zoomIn(): void;
  zoomOut(): void;
  zoomToFit(): void;
  zoomToSelection(): void;
  resetZoom(): void;
  exportSelectionPng(): void;
}
