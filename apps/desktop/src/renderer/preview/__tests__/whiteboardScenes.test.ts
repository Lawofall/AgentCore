import { parseScene, serializeScene } from "@/whiteboard";
import { describe, expect, it } from "vitest";
import { WHITEBOARD_SCENES } from "../whiteboardScenes";

describe("WHITEBOARD_SCENES", () => {
  it("exposes scenes with unique ids and non-empty elements", () => {
    expect(WHITEBOARD_SCENES.length).toBeGreaterThan(0);
    const ids = WHITEBOARD_SCENES.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const s of WHITEBOARD_SCENES) {
      expect(s.elements.length).toBeGreaterThan(0);
    }
  });

  it("each scene is an exportable board vector (round-trips through serialize/parse)", () => {
    for (const s of WHITEBOARD_SCENES) {
      const parsed = parseScene(serializeScene(s.elements));
      expect(parsed.elements.length).toBe(s.elements.length);
    }
  });

  it("rotation scene has a rotated, preselected element (WB-007)", () => {
    const scene = WHITEBOARD_SCENES.find((s) => s.id === "board_rotation");
    const rotated = scene?.elements.find((e) => (e.rotation ?? 0) !== 0);
    expect(rotated).toBeDefined();
    expect(scene?.selectedIds).toContain(rotated?.id);
  });

  it("dagre scene spreads chained nodes left-to-right (real layout)", () => {
    const scene = WHITEBOARD_SCENES.find((s) => s.id === "board_dagre_layout");
    const boxes = (scene?.elements ?? []).filter((e) => e.type === "rectangle");
    const xs = new Set(boxes.map((b) => Math.round(b.x)));
    expect(xs.size).toBeGreaterThan(1);
  });
});
