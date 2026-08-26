/** Placement relative to the tab under the pointer. */
export type ReorderPlace = "before" | "after";

/** Axis used to decide before/after from the pointer vs the hit rect. */
export type ReorderAxis = "x" | "y";

/** Midpoint split along `axis`. Default tab strips use `"x"`. */
export function placeAlongAxis(
  axis: ReorderAxis,
  clientX: number,
  clientY: number,
  rect: Pick<DOMRect, "left" | "top" | "width" | "height">,
): ReorderPlace {
  if (axis === "y") {
    return clientY < rect.top + rect.height / 2 ? "before" : "after";
  }
  return clientX < rect.left + rect.width / 2 ? "before" : "after";
}

/**
 * Move `fromId` next to `overId` (`before` / `after`).
 * Unknown ids or a no-op move return a shallow copy of `ids`.
 */
export function moveItem(
  ids: readonly string[],
  fromId: string,
  overId: string,
  place: ReorderPlace,
): string[] {
  if (fromId === overId) return [...ids];
  const fromIndex = ids.indexOf(fromId);
  const overIndex = ids.indexOf(overId);
  if (fromIndex < 0 || overIndex < 0) return [...ids];

  const next = ids.filter((id) => id !== fromId);
  let insertAt = next.indexOf(overId);
  if (insertAt < 0) return [...ids];
  if (place === "after") insertAt += 1;
  next.splice(insertAt, 0, fromId);
  return next;
}

/** Attribute on interactive chrome (close, menu) that must not start a tab drag. */
export const NO_TAB_DRAG_ATTR = "data-no-tab-drag";

/** Pixel distance before pointer motion becomes a drag. */
export const TAB_DRAG_THRESHOLD_PX = 5;
