/** Canvas 2D renderer (AI协作白板.md §六 渲染层). Draws the scene under the viewport
 * transform, then selection chrome in screen space for crisp 1px lines. */

import type { Palette } from "./colors";
import {
  type Box,
  arrowEndpoints,
  elementBox,
  isLinear,
  worldToScreen,
} from "./geometry";
import type { ImageCache } from "./images";
import type { SceneElement, Viewport } from "./types";

const GRID = 24;
const STROKE_W = 2;
const HANDLE = 9;
/** Screen-space offset of the rotation handle above a single selection box. */
const ROTATE_HANDLE_OFFSET = 28;

export interface RenderInput {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  dpr: number;
  viewport: Viewport;
  elements: readonly SceneElement[];
  palette: Palette;
  selectedIds: ReadonlySet<string>;
  /** Suppress drawing this element's text (its edit overlay is showing it). */
  editingId: string | null;
  /** Active marquee box (world coords) while box-selecting. */
  marquee: Box | null;
  /** Decoded-image source for `image` elements (omitted → images draw as placeholders). */
  images?: ImageCache;
  /** Active snap/alignment guide segments (world coords) to draw while dragging. */
  guides?: ReadonlyArray<[number, number, number, number]>;
}

export function renderScene(input: RenderInput): void {
  const { ctx, width, height, dpr, viewport, elements, palette } = input;
  const byId = new Map(elements.map((e) => [e.id, e]));

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = palette.background;
  ctx.fillRect(0, 0, width, height);

  drawGrid(ctx, width, height, viewport, palette);

  ctx.save();
  ctx.translate(viewport.panX, viewport.panY);
  ctx.scale(viewport.zoom, viewport.zoom);
  for (const el of elements)
    drawElement(ctx, el, palette, byId, input.editingId, input.images);
  ctx.restore();

  drawSelection(input, byId);
}

function drawGrid(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  v: Viewport,
  palette: Palette,
): void {
  const step = GRID * v.zoom;
  if (step < 8) return; // too dense to be useful
  const startX = ((v.panX % step) + step) % step;
  const startY = ((v.panY % step) + step) % step;
  ctx.save();
  ctx.fillStyle = palette.border;
  ctx.globalAlpha = 0.6;
  for (let x = startX; x < width; x += step) {
    for (let y = startY; y < height; y += step) {
      ctx.beginPath();
      ctx.arc(x, y, 1, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.restore();
}

function roundRectPath(
  ctx: CanvasRenderingContext2D,
  b: Box,
  radius: number,
): void {
  const r = Math.min(radius, b.width / 2, b.height / 2);
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") {
    ctx.roundRect(b.x, b.y, b.width, b.height, r);
  } else {
    ctx.moveTo(b.x + r, b.y);
    ctx.arcTo(b.x + b.width, b.y, b.x + b.width, b.y + b.height, r);
    ctx.arcTo(b.x + b.width, b.y + b.height, b.x, b.y + b.height, r);
    ctx.arcTo(b.x, b.y + b.height, b.x, b.y, r);
    ctx.arcTo(b.x, b.y, b.x + b.width, b.y, r);
    ctx.closePath();
  }
}

function drawElement(
  ctx: CanvasRenderingContext2D,
  el: SceneElement,
  palette: Palette,
  byId: ReadonlyMap<string, SceneElement>,
  editingId: string | null,
  images?: ImageCache,
): void {
  const b = elementBox(el);
  // Rotation (box-like elements only) + opacity wrap one save/restore around the whole draw.
  const rot =
    !isLinear(el.type) && el.type !== "freedraw" ? (el.rotation ?? 0) : 0;
  const opacity = el.opacity ?? 1;
  const layered = rot !== 0 || opacity < 1;
  if (layered) {
    ctx.save();
    if (opacity < 1) ctx.globalAlpha = opacity;
    if (rot !== 0) {
      const cx = b.x + b.width / 2;
      const cy = b.y + b.height / 2;
      ctx.translate(cx, cy);
      ctx.rotate(rot);
      ctx.translate(-cx, -cy);
    }
  }
  ctx.lineWidth = el.strokeWidth ?? STROKE_W;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  // Dash scales with width so a thick dashed line reads as dashed, not nearly-solid. The
  // element loop is wrapped in save/restore (renderScene), so this never leaks to selection
  // chrome; each element also re-sets it here, so a solid element after a dashed one resets.
  ctx.setLineDash(
    el.strokeStyle === "dashed" ? [ctx.lineWidth * 2.5, ctx.lineWidth * 2] : [],
  );

  switch (el.type) {
    case "rectangle":
    case "frame": {
      ctx.strokeStyle = el.stroke ?? palette.foreground;
      if (el.fill) {
        ctx.fillStyle = el.fill;
        roundRectPath(ctx, b, 8);
        ctx.fill();
      }
      roundRectPath(ctx, b, 8);
      ctx.stroke();
      drawLabel(ctx, el, b, palette, editingId);
      break;
    }
    case "sticky": {
      const base = el.fill ?? palette.warning;
      ctx.save();
      ctx.globalAlpha = 0.22 * opacity;
      ctx.fillStyle = base;
      roundRectPath(ctx, b, 6);
      ctx.fill();
      ctx.restore();
      ctx.globalAlpha = 0.55 * opacity;
      ctx.strokeStyle = base;
      roundRectPath(ctx, b, 6);
      ctx.stroke();
      ctx.globalAlpha = opacity;
      drawLabel(ctx, el, b, palette, editingId);
      break;
    }
    case "ellipse": {
      ctx.strokeStyle = el.stroke ?? palette.foreground;
      ctx.beginPath();
      ctx.ellipse(
        b.x + b.width / 2,
        b.y + b.height / 2,
        b.width / 2,
        b.height / 2,
        0,
        0,
        Math.PI * 2,
      );
      if (el.fill) {
        ctx.fillStyle = el.fill;
        ctx.fill();
      }
      ctx.stroke();
      drawLabel(ctx, el, b, palette, editingId);
      break;
    }
    case "diamond": {
      ctx.strokeStyle = el.stroke ?? palette.foreground;
      const cx = b.x + b.width / 2;
      const cy = b.y + b.height / 2;
      ctx.beginPath();
      ctx.moveTo(cx, b.y);
      ctx.lineTo(b.x + b.width, cy);
      ctx.lineTo(cx, b.y + b.height);
      ctx.lineTo(b.x, cy);
      ctx.closePath();
      if (el.fill) {
        ctx.fillStyle = el.fill;
        ctx.fill();
      }
      ctx.stroke();
      drawLabel(ctx, el, b, palette, editingId);
      break;
    }
    case "text": {
      if (el.id === editingId) break;
      const size = el.fontSize ?? 20;
      ctx.fillStyle = el.stroke ?? el.fill ?? palette.foreground;
      ctx.font = `${size}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textBaseline = "top";
      const align = el.textAlign ?? "left";
      ctx.textAlign = align;
      const tx =
        align === "center"
          ? el.x + el.width / 2
          : align === "right"
            ? el.x + el.width
            : el.x;
      const lines = (el.text ?? "").split("\n");
      lines.forEach((line, i) => ctx.fillText(line, tx, el.y + i * size * 1.3));
      ctx.textAlign = "left";
      break;
    }
    case "freedraw": {
      ctx.strokeStyle = el.stroke ?? palette.foreground;
      drawFreehand(ctx, el);
      break;
    }
    case "image": {
      drawImage(ctx, el, b, palette, images);
      break;
    }
    case "arrow": {
      ctx.strokeStyle = el.stroke ?? palette.mutedForeground;
      drawLinear(ctx, el, byId, palette, true);
      break;
    }
    case "line": {
      ctx.strokeStyle = el.stroke ?? palette.mutedForeground;
      drawLinear(ctx, el, byId, palette, false);
      break;
    }
  }

  if (layered) ctx.restore();
  if (el.locked) drawLockBadge(ctx, b);
}

/** Tiny padlock mark at an element's top-right so locked elements are recognizable (they
 * can't be selected by click; right-click →「解锁」/「解锁全部」). Drawn unrotated. */
function drawLockBadge(ctx: CanvasRenderingContext2D, b: Box): void {
  const s = 7;
  const x = b.x + b.width - s - 2;
  const y = b.y + 2;
  ctx.save();
  ctx.setLineDash([]);
  ctx.globalAlpha = 0.55;
  ctx.fillStyle = "#000";
  ctx.fillRect(x, y + s * 0.45, s, s * 0.55);
  ctx.strokeStyle = "#000";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(x + s / 2, y + s * 0.45, s * 0.3, Math.PI, 0);
  ctx.stroke();
  ctx.restore();
}

function drawLabel(
  ctx: CanvasRenderingContext2D,
  el: SceneElement,
  b: Box,
  palette: Palette,
  editingId: string | null,
): void {
  if (!el.text || el.id === editingId) return;
  const size = el.fontSize ?? 16;
  ctx.save();
  ctx.fillStyle = palette.foreground;
  ctx.font = `${size}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const lines = wrapText(ctx, el.text, b.width - 16);
  const lineH = size * 1.3;
  const startY = b.y + b.height / 2 - ((lines.length - 1) * lineH) / 2;
  lines.forEach((line, i) =>
    ctx.fillText(line, b.x + b.width / 2, startY + i * lineH),
  );
  ctx.restore();
}

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
): string[] {
  const out: string[] = [];
  for (const paragraph of text.split("\n")) {
    let line = "";
    for (const ch of paragraph) {
      if (ctx.measureText(line + ch).width > maxWidth && line) {
        out.push(line);
        line = ch;
      } else {
        line += ch;
      }
    }
    out.push(line);
  }
  return out;
}

function drawImage(
  ctx: CanvasRenderingContext2D,
  el: SceneElement,
  b: Box,
  palette: Palette,
  images?: ImageCache,
): void {
  const img = images?.get(el.src);
  if (img) {
    ctx.save();
    roundRectPath(ctx, b, 4);
    ctx.clip();
    ctx.drawImage(img, b.x, b.y, b.width, b.height);
    ctx.restore();
    return;
  }
  // Loading (or a broken / absent src): a muted placeholder so the box is still selectable.
  ctx.save();
  ctx.fillStyle = palette.muted;
  roundRectPath(ctx, b, 4);
  ctx.fill();
  ctx.strokeStyle = palette.border;
  ctx.stroke();
  ctx.fillStyle = palette.mutedForeground;
  ctx.font = "13px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("图片", b.x + b.width / 2, b.y + b.height / 2);
  ctx.restore();
}

function drawFreehand(ctx: CanvasRenderingContext2D, el: SceneElement): void {
  const pts = el.points ?? [];
  if (pts.length === 0) return;

  const first = pts[0];
  const last = pts[pts.length - 1];
  const closed =
    pts.length >= 3 && Math.hypot(first[0] - last[0], first[1] - last[1]) < 2;

  ctx.beginPath();
  ctx.moveTo(el.x + pts[0][0], el.y + pts[0][1]);
  if (closed) {
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(el.x + pts[i][0], el.y + pts[i][1]);
    }
    ctx.closePath();
    ctx.fill();
    return;
  }

  if (pts.length === 1) {
    ctx.lineTo(el.x + pts[0][0] + 0.1, el.y + pts[0][1] + 0.1);
  } else {
    for (let i = 1; i < pts.length - 1; i++) {
      const x1 = el.x + pts[i][0];
      const y1 = el.y + pts[i][1];
      const x2 = el.x + pts[i + 1][0];
      const y2 = el.y + pts[i + 1][1];
      ctx.quadraticCurveTo(x1, y1, (x1 + x2) / 2, (y1 + y2) / 2);
    }
    const lastPt = pts[pts.length - 1];
    ctx.lineTo(el.x + lastPt[0], el.y + lastPt[1]);
  }
  ctx.stroke();
}

function drawLinear(
  ctx: CanvasRenderingContext2D,
  el: SceneElement,
  byId: ReadonlyMap<string, SceneElement>,
  palette: Palette,
  withHead: boolean,
): void {
  const [a, b] = arrowEndpoints(el, byId);
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.stroke();
  ctx.setLineDash([]); // a dashed shaft keeps a crisp, solid arrowhead
  if (withHead) {
    const angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
    const head = 12;
    ctx.beginPath();
    ctx.moveTo(b[0], b[1]);
    ctx.lineTo(
      b[0] - head * Math.cos(angle - Math.PI / 6),
      b[1] - head * Math.sin(angle - Math.PI / 6),
    );
    ctx.moveTo(b[0], b[1]);
    ctx.lineTo(
      b[0] - head * Math.cos(angle + Math.PI / 6),
      b[1] - head * Math.sin(angle + Math.PI / 6),
    );
    ctx.stroke();
  }
  if (el.text) {
    ctx.save();
    ctx.fillStyle = palette.foreground;
    ctx.font = "13px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(el.text, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 8);
    ctx.restore();
  }
}

/** Resize handle positions for a single-element screen box, in draw order. */
export function handlePositions(
  box: Box,
): Array<{ id: string; x: number; y: number }> {
  const { x, y, width: w, height: h } = box;
  return [
    { id: "nw", x, y },
    { id: "n", x: x + w / 2, y },
    { id: "ne", x: x + w, y },
    { id: "e", x: x + w, y: y + h / 2 },
    { id: "se", x: x + w, y: y + h },
    { id: "s", x: x + w / 2, y: y + h },
    { id: "sw", x, y: y + h },
    { id: "w", x, y: y + h / 2 },
  ];
}

function elementScreenBox(
  el: SceneElement,
  v: Viewport,
  byId: ReadonlyMap<string, SceneElement>,
): Box {
  let b: Box;
  if (isLinear(el.type)) {
    // Bound linear elements reroute to their endpoints' live geometry, so derive the box from
    // the resolved endpoints (the stored x/y/w/h is only kept in sync for unbound ones).
    const [a, c] = arrowEndpoints(el, byId);
    b = {
      x: Math.min(a[0], c[0]),
      y: Math.min(a[1], c[1]),
      width: Math.abs(c[0] - a[0]),
      height: Math.abs(c[1] - a[1]),
    };
  } else {
    b = elementBox(el);
  }
  const [sx, sy] = worldToScreen(v, b.x, b.y);
  return { x: sx, y: sy, width: b.width * v.zoom, height: b.height * v.zoom };
}

/** Screen-space union box of several elements (for the multi-selection resize frame). */
function unionScreenBox(
  els: readonly SceneElement[],
  v: Viewport,
  byId: ReadonlyMap<string, SceneElement>,
): Box {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const el of els) {
    const sb = elementScreenBox(el, v, byId);
    minX = Math.min(minX, sb.x);
    minY = Math.min(minY, sb.y);
    maxX = Math.max(maxX, sb.x + sb.width);
    maxY = Math.max(maxY, sb.y + sb.height);
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

/** Resize-handle screen positions for the current selection — shared by the renderer (to
 * draw them) and the engine (to hit-test). Empty for a lone linear / freedraw element (no
 * box handles); a single box element rotates its handles by its `rotation`; a multi-selection
 * uses an axis-aligned union frame. */
export function selectionHandlesScreen(
  selected: readonly SceneElement[],
  v: Viewport,
  byId: ReadonlyMap<string, SceneElement>,
): Array<{ id: string; x: number; y: number }> {
  if (selected.length === 0) return [];
  if (selected.length === 1) {
    const el = selected[0];
    if (isLinear(el.type) || el.type === "freedraw") return [];
    const sb = elementScreenBox(el, v, byId);
    const hs = handlePositions(sb);
    const rot = el.rotation ?? 0;
    const cx = sb.x + sb.width / 2;
    const cy = sb.y + sb.height / 2;
    const mapped =
      rot === 0
        ? hs
        : hs.map((h) => {
            const dx = h.x - cx;
            const dy = h.y - cy;
            return {
              id: h.id,
              x: cx + dx * Math.cos(rot) - dy * Math.sin(rot),
              y: cy + dx * Math.sin(rot) + dy * Math.cos(rot),
            };
          });
    const ox = 0;
    const oy = -sb.height / 2 - ROTATE_HANDLE_OFFSET;
    const rhx = cx + ox * Math.cos(rot) - oy * Math.sin(rot);
    const rhy = cy + ox * Math.sin(rot) + oy * Math.cos(rot);
    return [...mapped, { id: "rotate", x: rhx, y: rhy }];
  }
  return handlePositions(unionScreenBox(selected, v, byId));
}

function drawSelection(
  input: RenderInput,
  byId: ReadonlyMap<string, SceneElement>,
): void {
  const { ctx, viewport, selectedIds, palette, marquee, guides } = input;
  ctx.setTransform(input.dpr, 0, 0, input.dpr, 0, 0);

  if (guides && guides.length > 0) {
    ctx.save();
    ctx.strokeStyle = palette.primary;
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    for (const [x1, y1, x2, y2] of guides) {
      const [sx1, sy1] = worldToScreen(viewport, x1, y1);
      const [sx2, sy2] = worldToScreen(viewport, x2, y2);
      ctx.beginPath();
      ctx.moveTo(sx1, sy1);
      ctx.lineTo(sx2, sy2);
      ctx.stroke();
    }
    ctx.restore();
  }

  if (marquee) {
    const [sx, sy] = worldToScreen(viewport, marquee.x, marquee.y);
    ctx.save();
    ctx.strokeStyle = palette.primary;
    ctx.fillStyle = palette.primary;
    ctx.globalAlpha = 0.08;
    ctx.fillRect(
      sx,
      sy,
      marquee.width * viewport.zoom,
      marquee.height * viewport.zoom,
    );
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(
      sx,
      sy,
      marquee.width * viewport.zoom,
      marquee.height * viewport.zoom,
    );
    ctx.restore();
  }

  if (selectedIds.size === 0) return;
  const selected = [...selectedIds]
    .map((id) => byId.get(id))
    .filter((e): e is SceneElement => !!e);
  if (selected.length === 0) return;

  ctx.save();
  ctx.strokeStyle = palette.primary;
  ctx.lineWidth = 1.5;

  for (const el of selected) {
    const sb = elementScreenBox(el, viewport, byId);
    const rot =
      !isLinear(el.type) && el.type !== "freedraw" ? (el.rotation ?? 0) : 0;
    if (rot === 0) {
      ctx.strokeRect(sb.x - 1, sb.y - 1, sb.width + 2, sb.height + 2);
    } else {
      const cx = sb.x + sb.width / 2;
      const cy = sb.y + sb.height / 2;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(rot);
      ctx.strokeRect(
        -sb.width / 2 - 1,
        -sb.height / 2 - 1,
        sb.width + 2,
        sb.height + 2,
      );
      ctx.restore();
    }
  }
  // When multiple are selected, also outline the union frame the resize handles act on.
  if (selected.length > 1) {
    const u = unionScreenBox(selected, viewport, byId);
    ctx.save();
    ctx.globalAlpha = 0.6;
    ctx.strokeRect(u.x, u.y, u.width, u.height);
    ctx.restore();
  }

  ctx.fillStyle = palette.background;
  for (const h of selectionHandlesScreen(selected, viewport, byId)) {
    if (h.id === "rotate") {
      const el = selected.length === 1 ? selected[0] : null;
      if (el) {
        const sb = elementScreenBox(el, viewport, byId);
        const cx = sb.x + sb.width / 2;
        const cy = sb.y + sb.height / 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(h.x, h.y);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(h.x, h.y, HANDLE / 2 + 1, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      continue;
    }
    ctx.beginPath();
    ctx.rect(h.x - HANDLE / 2, h.y - HANDLE / 2, HANDLE, HANDLE);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}
