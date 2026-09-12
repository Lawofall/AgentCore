/** Serialize / parse the persisted scene blob (our own format, §七 典范模型 = 场景 JSON). */

import {
  type BoardScenePayload,
  type ElementType,
  SCENE_FORMAT,
  SCENE_SCHEMA_VERSION,
  type SceneElement,
  type Viewport,
} from "./types";

const KNOWN_TYPES: ReadonlySet<ElementType> = new Set<ElementType>([
  "rectangle",
  "ellipse",
  "diamond",
  "sticky",
  "text",
  "freedraw",
  "image",
  "arrow",
  "line",
  "frame",
]);

export function serializeScene(
  elements: readonly SceneElement[],
  viewport?: Viewport,
): BoardScenePayload {
  return {
    format: SCENE_FORMAT,
    schemaVersion: SCENE_SCHEMA_VERSION,
    elements: elements as SceneElement[],
    appState: viewport ? { viewport } : undefined,
  };
}

/** Parse a stored scene. Unknown / legacy blobs (e.g. an old Excalidraw scene) yield an
 * empty canvas — we deliberately do NOT migrate (dev phase, no compat layer, §决策). */
export function parseScene(raw: unknown): {
  elements: SceneElement[];
  viewport?: Viewport;
} {
  if (!raw || typeof raw !== "object") return { elements: [] };
  const obj = raw as Record<string, unknown>;
  if (obj.format !== SCENE_FORMAT || !Array.isArray(obj.elements)) {
    return { elements: [] };
  }
  const elements: SceneElement[] = [];
  for (const item of obj.elements as unknown[]) {
    const el = normalizeElement(item);
    if (el) elements.push(el);
  }
  const appState = obj.appState as Record<string, unknown> | undefined;
  return { elements, viewport: readViewport(appState?.viewport) };
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function normalizeElement(item: unknown): SceneElement | null {
  if (!item || typeof item !== "object") return null;
  const o = item as Record<string, unknown>;
  const type = o.type as ElementType;
  if (typeof o.id !== "string" || !KNOWN_TYPES.has(type)) return null;
  const points = Array.isArray(o.points)
    ? (o.points as unknown[])
        .filter((p): p is [number, number] => Array.isArray(p) && p.length >= 2)
        .map((p) => [num(p[0]), num(p[1])] as [number, number])
    : undefined;
  return {
    id: o.id,
    type,
    x: num(o.x),
    y: num(o.y),
    width: num(o.width),
    height: num(o.height),
    text: typeof o.text === "string" ? o.text : undefined,
    fill: typeof o.fill === "string" ? o.fill : undefined,
    stroke: typeof o.stroke === "string" ? o.stroke : undefined,
    strokeWidth: typeof o.strokeWidth === "number" ? o.strokeWidth : undefined,
    strokeStyle:
      o.strokeStyle === "dashed" || o.strokeStyle === "solid"
        ? o.strokeStyle
        : undefined,
    fontSize: typeof o.fontSize === "number" ? o.fontSize : undefined,
    textAlign:
      o.textAlign === "left" ||
      o.textAlign === "center" ||
      o.textAlign === "right"
        ? o.textAlign
        : undefined,
    rotation: typeof o.rotation === "number" ? o.rotation : undefined,
    opacity: typeof o.opacity === "number" ? o.opacity : undefined,
    locked: o.locked === true ? true : undefined,
    src: typeof o.src === "string" ? o.src : undefined,
    points,
    start: isBinding(o.start) ? { id: o.start.id } : undefined,
    end: isBinding(o.end) ? { id: o.end.id } : undefined,
    groupIds: Array.isArray(o.groupIds)
      ? (o.groupIds as unknown[]).filter(
          (g): g is string => typeof g === "string",
        )
      : undefined,
    schemaVersion:
      typeof o.schemaVersion === "number"
        ? o.schemaVersion
        : SCENE_SCHEMA_VERSION,
  };
}

function isBinding(v: unknown): v is { id: string } {
  return (
    !!v &&
    typeof v === "object" &&
    typeof (v as { id?: unknown }).id === "string"
  );
}

function readViewport(v: unknown): Viewport | undefined {
  if (!v || typeof v !== "object") return undefined;
  const o = v as Record<string, unknown>;
  if (
    typeof o.panX !== "number" ||
    typeof o.panY !== "number" ||
    typeof o.zoom !== "number"
  ) {
    return undefined;
  }
  return { panX: o.panX, panY: o.panY, zoom: o.zoom };
}
