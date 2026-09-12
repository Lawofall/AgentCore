/**
 * In-place text edit — layout math for the React overlay (AI协作白板.md).
 *
 * The engine stores a {@link TextEditSession}; the host mounts a textarea on the element's
 * screen box. This module is the single source for "where does the overlay sit" so pan/zoom/
 * rotation stay in lockstep with the canvas paint. No DOM here.
 */

import { elementBox, isLinear } from "./geometry";
import type {
  SceneElement,
  TextAlign,
  TextEditSession,
  Viewport,
} from "./types";

/** Matches `drawElement` / `commitText` fallbacks. */
const FONT_TEXT = 18;
const FONT_TEXT_PAINT = 20;
const FONT_LABEL = 16;

export function isTextEditable(el: SceneElement): boolean {
  switch (el.type) {
    case "text":
    case "sticky":
    case "rectangle":
    case "ellipse":
    case "diamond":
    case "frame":
      return true;
    default:
      return false;
  }
}

export interface EditOverlayLayout {
  left: number;
  top: number;
  width: number;
  height: number;
  fontSize: number;
  rotate: number;
  color: string;
  align: TextAlign;
  /** CSS padding (screen px) — labels inset like canvas `wrapText` (16 world units total). */
  pad: number;
  /** When true, the textarea grows with content (`text` / new text). */
  grow: boolean;
}

export function editOverlayLayout(
  session: TextEditSession,
  el: SceneElement | null,
  vp: Viewport,
): EditOverlayLayout {
  if (el) {
    const b = elementBox(el);
    const grow = el.type === "text";
    const baseFont = grow
      ? (el.fontSize ?? FONT_TEXT_PAINT)
      : (el.fontSize ?? FONT_LABEL);
    return {
      left: b.x * vp.zoom + vp.panX,
      top: b.y * vp.zoom + vp.panY,
      width: Math.max(1, b.width * vp.zoom),
      height: Math.max(1, b.height * vp.zoom),
      fontSize: baseFont * vp.zoom,
      rotate:
        isLinear(el.type) || el.type === "freedraw" ? 0 : (el.rotation ?? 0),
      color: grow
        ? (el.stroke ?? el.fill ?? "var(--foreground)")
        : "var(--foreground)",
      align: grow ? (el.textAlign ?? "left") : "center",
      pad: grow ? 0 : 8 * vp.zoom,
      grow,
    };
  }
  return {
    left: session.world[0] * vp.zoom + vp.panX,
    top: session.world[1] * vp.zoom + vp.panY,
    width: 80,
    height: FONT_TEXT * vp.zoom * 1.3 + 4,
    fontSize: FONT_TEXT * vp.zoom,
    rotate: 0,
    color: "var(--foreground)",
    align: "left",
    pad: 0,
    grow: true,
  };
}

export function sameOverlayLayout(
  a: EditOverlayLayout,
  b: EditOverlayLayout,
): boolean {
  return (
    a.left === b.left &&
    a.top === b.top &&
    a.width === b.width &&
    a.height === b.height &&
    a.fontSize === b.fontSize &&
    a.rotate === b.rotate &&
    a.color === b.color &&
    a.align === b.align &&
    a.pad === b.pad &&
    a.grow === b.grow
  );
}
