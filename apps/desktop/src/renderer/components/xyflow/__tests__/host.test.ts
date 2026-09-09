import { describe, expect, it } from "vitest";
import { XYFLOW_PRO_OPTIONS, xyflowCameraKey } from "../host";

describe("xyflowCameraKey", () => {
  it("empty until bbox exists", () => {
    expect(xyflowCameraKey(null, 640)).toBe("");
    expect(xyflowCameraKey(undefined, 640)).toBe("");
  });

  it("bbox-only key when colWidth is 0 (fullscreen fit)", () => {
    expect(xyflowCameraKey({ width: 400, height: 200 }, 0)).toBe(
      "400.00x200.00",
    );
  });

  it("includes rounded column width for fit-to-width", () => {
    expect(xyflowCameraKey({ width: 400, height: 200 }, 641.7)).toBe(
      "400.00x200.00@642",
    );
  });

  it("same geometry → same key (object identity must not matter)", () => {
    const a = xyflowCameraKey({ width: 10.1, height: 2 }, 100);
    const b = xyflowCameraKey({ width: 10.1, height: 2 }, 100);
    expect(a).toBe(b);
  });
});

describe("XYFLOW_PRO_OPTIONS", () => {
  it("stable module identity", () => {
    expect(XYFLOW_PRO_OPTIONS).toBe(XYFLOW_PRO_OPTIONS);
    expect(XYFLOW_PRO_OPTIONS.hideAttribution).toBe(true);
  });
});
