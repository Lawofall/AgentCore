import { describe, expect, it } from "vitest";
import { editOverlayLayout, isTextEditable } from "../textEditor";
import type { SceneElement } from "../types";

const base = {
  x: 0,
  y: 0,
  width: 100,
  height: 40,
  schemaVersion: 1 as const,
};

function el(
  type: SceneElement["type"],
  extra: Partial<SceneElement> = {},
): SceneElement {
  return { id: "e", type, ...base, ...extra };
}

describe("isTextEditable", () => {
  it("allows labels and text, not arrows or images", () => {
    expect(isTextEditable(el("sticky"))).toBe(true);
    expect(isTextEditable(el("text"))).toBe(true);
    expect(isTextEditable(el("rectangle"))).toBe(true);
    expect(isTextEditable(el("ellipse"))).toBe(true);
    expect(isTextEditable(el("diamond"))).toBe(true);
    expect(isTextEditable(el("frame"))).toBe(true);
    expect(isTextEditable(el("arrow"))).toBe(false);
    expect(isTextEditable(el("line"))).toBe(false);
    expect(isTextEditable(el("image"))).toBe(false);
    expect(isTextEditable(el("freedraw"))).toBe(false);
  });
});

describe("editOverlayLayout", () => {
  const vp = { panX: 10, panY: 20, zoom: 2 };

  it("scales a sticky with pan/zoom, rotation, and centered label padding", () => {
    const sticky = el("sticky", { x: 5, y: 6, rotation: 0.5 });
    expect(
      editOverlayLayout({ id: "e", world: [5, 6] }, sticky, vp),
    ).toMatchObject({
      left: 20,
      top: 32,
      width: 200,
      height: 80,
      fontSize: 32,
      rotate: 0.5,
      align: "center",
      grow: false,
      pad: 16,
    });
  });

  it("grows a text element and uses its alignment + color", () => {
    const text = el("text", {
      fontSize: 18,
      textAlign: "right",
      stroke: "oklch(0.2 0 0)",
    });
    expect(
      editOverlayLayout({ id: "e", world: [0, 0] }, text, vp),
    ).toMatchObject({
      grow: true,
      align: "right",
      color: "oklch(0.2 0 0)",
      fontSize: 36,
      pad: 0,
      rotate: 0,
    });
  });

  it("places new text at the click in screen space", () => {
    const layout = editOverlayLayout({ id: null, world: [3, 4] }, null, vp);
    expect(layout.left).toBe(16);
    expect(layout.top).toBe(28);
    expect(layout.grow).toBe(true);
    expect(layout.align).toBe("left");
  });
});
