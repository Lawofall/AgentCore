/**
 * Pure selection transforms (AI协作白板.md §六) — clipboard / group / z-order / style /
 * nudge, extracted from the engine so they stay testable without a canvas.
 *
 * Each takes the scene + the selected id set (+ params) and returns a NEW element array;
 * touched elements are deep-cloned so the caller's history snapshot never aliases the live
 * scene. The engine wraps these thin: snapshot history → swap in the result → notify.
 */

import { cloneElement } from "./clone";
import { elementBox, isLinear } from "./geometry";
import type { SceneElement, StrokeStyle, TextAlign } from "./types";

/** Deep-clone `el` translated by (dx, dy). Arrow points are absolute world coords, so they
 * move too (freedraw's are relative to x/y and ride along for free). Shared by nudge / align
 * / distribute — every selection move funnels through here. */
function shift(el: SceneElement, dx: number, dy: number): SceneElement {
  const copy = cloneElement(el);
  copy.x += dx;
  copy.y += dy;
  if (copy.type === "arrow" && copy.points) {
    copy.points = copy.points.map(
      ([px, py]) => [px + dx, py + dy] as [number, number],
    );
  }
  return copy;
}

/** Expand `ids` to include every element sharing a group with any of them, so a group is
 * always selected as a whole (grouping is flat — one groupId per member). */
export function withGroup(
  elements: readonly SceneElement[],
  ids: readonly string[],
): string[] {
  const byId = new Map(elements.map((e) => [e.id, e]));
  const groups = new Set<string>();
  for (const id of ids) {
    for (const g of byId.get(id)?.groupIds ?? []) groups.add(g);
  }
  if (groups.size === 0) return [...ids];
  const out = new Set(ids);
  for (const el of elements) {
    if (el.groupIds?.some((g) => groups.has(g))) out.add(el.id);
  }
  return [...out];
}

/** Move the selection to the front (drawn last) or back (drawn first) of the z-order. */
export function reorder(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  where: "front" | "back",
): SceneElement[] {
  const sel = elements.filter((e) => selected.has(e.id));
  const rest = elements.filter((e) => !selected.has(e.id));
  return where === "front" ? [...rest, ...sel] : [...sel, ...rest];
}

/** A style change for the selection: fill / stroke color, outline width, outline style.
 * A key absent from the patch is left untouched; `null` clears back to the type's default. */
export interface StylePatch {
  fill?: string | null;
  stroke?: string | null;
  strokeWidth?: number | null;
  strokeStyle?: StrokeStyle | null;
  textAlign?: TextAlign | null;
  opacity?: number | null;
}

/** Apply a {@link StylePatch} to the selection. `null` clears (back to the type's theme
 * default); a key absent from `patch` is left untouched. */
export function applyStyle(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  patch: StylePatch,
): SceneElement[] {
  const setFill = "fill" in patch;
  const setStroke = "stroke" in patch;
  const setWidth = "strokeWidth" in patch;
  const setStyle = "strokeStyle" in patch;
  const setAlign = "textAlign" in patch;
  const setOpacity = "opacity" in patch;
  return elements.map((e) => {
    if (!selected.has(e.id)) return e;
    const copy = cloneElement(e);
    if (setFill) copy.fill = patch.fill ?? undefined;
    if (setStroke) copy.stroke = patch.stroke ?? undefined;
    if (setWidth) copy.strokeWidth = patch.strokeWidth ?? undefined;
    if (setStyle) copy.strokeStyle = patch.strokeStyle ?? undefined;
    if (setAlign) copy.textAlign = patch.textAlign ?? undefined;
    if (setOpacity) copy.opacity = patch.opacity ?? undefined;
    return copy;
  });
}

/** Position / size / rotation of a single selected element. Multi-selection is a no-op
 * (align / distribute cover that). Rotation is ignored on linear / freedraw. */
export interface BoxPatch {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  rotation?: number;
}

export function patchBox(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  patch: BoxPatch,
): SceneElement[] {
  if (selected.size !== 1) return elements as SceneElement[];
  return elements.map((e) => {
    if (!selected.has(e.id)) return e;
    const copy = cloneElement(e);
    if (patch.x !== undefined) copy.x = patch.x;
    if (patch.y !== undefined) copy.y = patch.y;
    if (patch.width !== undefined) copy.width = Math.max(1, patch.width);
    if (patch.height !== undefined) copy.height = Math.max(1, patch.height);
    if (
      patch.rotation !== undefined &&
      !isLinear(e.type) &&
      e.type !== "freedraw"
    ) {
      copy.rotation = patch.rotation;
    }
    return copy;
  });
}

/** Set or clear the locked flag on selected elements. */
export function setLocked(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  locked: boolean,
): SceneElement[] {
  return elements.map((e) => {
    if (!selected.has(e.id)) return e;
    if (!!e.locked === locked) return e;
    const copy = cloneElement(e);
    copy.locked = locked || undefined;
    return copy;
  });
}

/** Unlock every locked element on the board. */
export function unlockAll(elements: readonly SceneElement[]): SceneElement[] {
  return elements.map((e) => {
    if (!e.locked) return e;
    const copy = cloneElement(e);
    copy.locked = undefined;
    return copy;
  });
}

/** Stamp one shared `groupId` onto every selected element (flat grouping, no nesting). */
export function setGroup(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  groupId: string,
): SceneElement[] {
  return elements.map((e) => {
    if (!selected.has(e.id)) return e;
    const copy = cloneElement(e);
    copy.groupIds = [groupId];
    return copy;
  });
}

/** Drop group membership from the selected elements. */
export function clearGroup(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
): SceneElement[] {
  return elements.map((e) => {
    if (!selected.has(e.id) || !e.groupIds?.length) return e;
    const copy = cloneElement(e);
    copy.groupIds = undefined;
    return copy;
  });
}

/** Translate the selection by (dx, dy). Arrow points are absolute, so they move too. */
export function nudge(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  dx: number,
  dy: number,
): SceneElement[] {
  return elements.map((e) => (selected.has(e.id) ? shift(e, dx, dy) : e));
}

/** Which edge / axis the selection's bounding boxes line up to. `centerX` / `centerY` use the
 * midline of the selection's overall bounds. */
export type AlignEdge =
  | "left"
  | "centerX"
  | "right"
  | "top"
  | "centerY"
  | "bottom";

/** Align every selected element to a shared edge / midline of the selection's overall bounds
 * (needs ≥2; a single element is already "aligned"). Each element keeps its size and only
 * shifts on the relevant axis, so the others' positions are untouched. */
export function align(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  edge: AlignEdge,
): SceneElement[] {
  const boxes = new Map(
    elements
      .filter((e) => selected.has(e.id))
      .map((e) => [e.id, elementBox(e)]),
  );
  if (boxes.size < 2) return [...elements];
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const b of boxes.values()) {
    minX = Math.min(minX, b.x);
    minY = Math.min(minY, b.y);
    maxX = Math.max(maxX, b.x + b.width);
    maxY = Math.max(maxY, b.y + b.height);
  }
  return elements.map((e) => {
    const b = boxes.get(e.id);
    if (!b) return e;
    let dx = 0;
    let dy = 0;
    switch (edge) {
      case "left":
        dx = minX - b.x;
        break;
      case "right":
        dx = maxX - (b.x + b.width);
        break;
      case "centerX":
        dx = (minX + maxX) / 2 - (b.x + b.width / 2);
        break;
      case "top":
        dy = minY - b.y;
        break;
      case "bottom":
        dy = maxY - (b.y + b.height);
        break;
      case "centerY":
        dy = (minY + maxY) / 2 - (b.y + b.height / 2);
        break;
    }
    return dx === 0 && dy === 0 ? e : shift(e, dx, dy);
  });
}

export type DistributeAxis = "x" | "y";

/** Even out the spacing between selected elements along an axis (needs ≥3 — the two extremes
 * stay put and the rest spread so the gaps between boxes are equal). Sizes are preserved. */
export function distribute(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  axis: DistributeAxis,
): SceneElement[] {
  const items = elements
    .filter((e) => selected.has(e.id))
    .map((e) => {
      const b = elementBox(e);
      return {
        id: e.id,
        start: axis === "x" ? b.x : b.y,
        size: axis === "x" ? b.width : b.height,
      };
    });
  if (items.length < 3) return [...elements];
  items.sort((p, q) => p.start + p.size / 2 - (q.start + q.size / 2));
  const min = items[0].start;
  const last = items[items.length - 1];
  const max = last.start + last.size;
  const totalSize = items.reduce((s, it) => s + it.size, 0);
  const gap = (max - min - totalSize) / (items.length - 1);
  const target = new Map<string, number>();
  let cursor = min;
  for (const it of items) {
    target.set(it.id, cursor);
    cursor += it.size + gap;
  }
  return elements.map((e) => {
    const t = target.get(e.id);
    if (t === undefined) return e;
    const b = elementBox(e);
    const d = t - (axis === "x" ? b.x : b.y);
    if (d === 0) return e;
    return axis === "x" ? shift(e, d, 0) : shift(e, 0, d);
  });
}

/** Move the selection one step toward the front / back of the z-order, hopping a single
 * unselected neighbor at a time (relative order within the selection is preserved). Element
 * refs are reused — only the array order changes — matching {@link reorder}. */
export function reorderStep(
  elements: readonly SceneElement[],
  selected: ReadonlySet<string>,
  dir: "forward" | "backward",
): SceneElement[] {
  const arr = [...elements];
  if (dir === "forward") {
    for (let i = arr.length - 2; i >= 0; i--) {
      if (selected.has(arr[i].id) && !selected.has(arr[i + 1].id)) {
        [arr[i], arr[i + 1]] = [arr[i + 1], arr[i]];
      }
    }
  } else {
    for (let i = 1; i < arr.length; i++) {
      if (selected.has(arr[i].id) && !selected.has(arr[i - 1].id)) {
        [arr[i], arr[i - 1]] = [arr[i - 1], arr[i]];
      }
    }
  }
  return arr;
}

/** Offset deep-copies of `src` with fresh ids; intra-set arrow bindings + group ids are
 * remapped so the copies are independent of the source. An arrow endpoint bound to an
 * element OUTSIDE the set keeps its original id (still a live element). Powers paste +
 * duplicate. */
export function copyWithOffset(
  src: readonly SceneElement[],
  offset: number,
): SceneElement[] {
  const idMap = new Map<string, string>();
  for (const e of src) idMap.set(e.id, `brd-${crypto.randomUUID()}`);
  const groupMap = new Map<string, string>();
  return src.map((e) => {
    const copy = cloneElement(e);
    copy.id = idMap.get(e.id) ?? copy.id;
    copy.x += offset;
    copy.y += offset;
    if (copy.type === "arrow" && copy.points) {
      copy.points = copy.points.map(
        ([px, py]) => [px + offset, py + offset] as [number, number],
      );
    }
    if (copy.start?.id)
      copy.start = { id: idMap.get(copy.start.id) ?? copy.start.id };
    if (copy.end?.id) copy.end = { id: idMap.get(copy.end.id) ?? copy.end.id };
    if (copy.groupIds?.length) {
      copy.groupIds = copy.groupIds.map((g) => {
        let ng = groupMap.get(g);
        if (!ng) {
          ng = `grp-${crypto.randomUUID()}`;
          groupMap.set(g, ng);
        }
        return ng;
      });
    }
    return copy;
  });
}
