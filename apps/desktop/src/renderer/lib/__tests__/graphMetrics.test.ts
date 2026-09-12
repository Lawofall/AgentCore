import {
  NODE_HEIGHT,
  NODE_WIDTH,
  buildNodeSizeMap,
} from "@agentcore/graph-layout";
import { describe, expect, it } from "vitest";

describe("graphMetrics · fixed layout footprint", () => {
  it("buildNodeSizeMap always uses NODE_WIDTH × NODE_HEIGHT", () => {
    const map = buildNodeSizeMap(["a", "b"], { a: 180 });
    expect(map.a).toEqual({ width: NODE_WIDTH, height: NODE_HEIGHT });
    expect(map.b).toEqual({ width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  it("ignores measuredHeights overlay (whiteboard: measure ≠ layout)", () => {
    const map = buildNodeSizeMap(["x"], { x: 999 });
    expect(map.x.height).toBe(NODE_HEIGHT);
  });
});
