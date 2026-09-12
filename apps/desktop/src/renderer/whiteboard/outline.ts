/**
 * Outline grouping for the whiteboard's left 画布导航 panel.
 * Labels prefer the element's own text; otherwise the type name.
 */

import type { ElementType, SceneElement } from "./types";

export const TYPE_LABELS: Record<ElementType, string> = {
  sticky: "便签",
  text: "文字",
  rectangle: "矩形",
  ellipse: "椭圆",
  diamond: "菱形",
  arrow: "箭头",
  line: "直线",
  freedraw: "画笔",
  image: "图片",
  frame: "区域框",
};

/** Display order in the outline (front-of-canvas types first in the list of groups). */
export const TYPE_ORDER: ElementType[] = [
  "sticky",
  "text",
  "rectangle",
  "ellipse",
  "diamond",
  "arrow",
  "line",
  "freedraw",
  "image",
  "frame",
];

export function elementOutlineLabel(el: SceneElement): string {
  const text = el.text?.trim();
  if (text) {
    const first = text.split("\n")[0] ?? text;
    return first.length > 24 ? `${first.slice(0, 24)}…` : first;
  }
  return TYPE_LABELS[el.type];
}

export interface OutlineGroup {
  type: ElementType;
  label: string;
  items: SceneElement[];
}

/** Group by type; within a group, front-most (later in the scene) is listed first. */
export function groupOutline(
  elements: readonly SceneElement[],
  filter: string,
): OutlineGroup[] {
  const q = filter.trim().toLowerCase();
  const buckets = new Map<ElementType, SceneElement[]>();
  for (const el of elements) {
    if (q) {
      const name = elementOutlineLabel(el).toLowerCase();
      const typeName = TYPE_LABELS[el.type].toLowerCase();
      if (!name.includes(q) && !typeName.includes(q)) continue;
    }
    const list = buckets.get(el.type);
    if (list) list.push(el);
    else buckets.set(el.type, [el]);
  }
  const groups: OutlineGroup[] = [];
  for (const type of TYPE_ORDER) {
    const items = buckets.get(type);
    if (!items?.length) continue;
    groups.push({
      type,
      label: TYPE_LABELS[type],
      items: [...items].reverse(),
    });
  }
  return groups;
}
