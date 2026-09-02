import { describe, expect, it } from "vitest";
import {
  MERMAID_INLINE_MAX_HEIGHT_REM,
  inlineMermaidBoxPx,
  mermaidInlineMaxHeightPx,
} from "../inlineMermaidBox";

describe("mermaidInlineMaxHeightPx", () => {
  it("caps at 36rem when the viewport is tall", () => {
    expect(mermaidInlineMaxHeightPx(1200, 16)).toBe(
      MERMAID_INLINE_MAX_HEIGHT_REM * 16,
    );
  });

  it("uses 50vh when that is below the rem cap", () => {
    expect(mermaidInlineMaxHeightPx(500, 16)).toBe(250);
  });
});

describe("inlineMermaidBoxPx", () => {
  it("keeps a chart that already fits", () => {
    expect(inlineMermaidBoxPx(400, 200, 700, 320)).toEqual({ w: 400, h: 200 });
  });

  it("does not upscale a small chart toward the column", () => {
    expect(inlineMermaidBoxPx(200, 80, 700, 320)).toEqual({ w: 200, h: 80 });
  });

  it("height-caps a tall TD stack and keeps aspect", () => {
    expect(inlineMermaidBoxPx(239, 677, 700, 320)).toEqual({
      w: Math.round(239 * (320 / 677)),
      h: 320,
    });
  });

  it("width-caps a wide chart", () => {
    expect(inlineMermaidBoxPx(1200, 400, 700, 320)).toEqual({
      w: 700,
      h: Math.round(400 * (700 / 1200)),
    });
  });

  it("returns null when native size is unknown", () => {
    expect(inlineMermaidBoxPx(0, 100, 700, 320)).toBeNull();
    expect(inlineMermaidBoxPx(100, 0, 700, 320)).toBeNull();
  });
});
