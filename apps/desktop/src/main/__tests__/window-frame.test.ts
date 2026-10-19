import { describe, expect, it } from "vitest";
import { fitToWorkArea, resolveStartupFramePreset } from "../window-frame";

describe("fitToWorkArea", () => {
  it("returns the same size when it already fits", () => {
    expect(fitToWorkArea(1280, 720, { width: 1920, height: 1080 })).toEqual({
      width: 1280,
      height: 720,
    });
  });

  it("scales down proportionally when larger than the work area", () => {
    const fitted = fitToWorkArea(1920, 1080, { width: 1366, height: 768 });
    expect(fitted.width).toBeLessThanOrEqual(1366);
    expect(fitted.height).toBeLessThanOrEqual(768);
    expect(fitted.width / fitted.height).toBeCloseTo(16 / 9, 2);
  });
});

describe("resolveStartupFramePreset", () => {
  it("keeps the saved lock when unpackaged", () => {
    expect(resolveStartupFramePreset("4:3-uxga", false)).toBe("4:3-uxga");
    expect(resolveStartupFramePreset("16:9-1080", false)).toBe("16:9-1080");
    expect(resolveStartupFramePreset("free", false)).toBe("free");
  });

  it("forces free in packaged builds so there is no lock without an unlock UI", () => {
    expect(resolveStartupFramePreset("4:3-uxga", true)).toBe("free");
    expect(resolveStartupFramePreset("16:9-1080", true)).toBe("free");
    expect(resolveStartupFramePreset("free", true)).toBe("free");
  });
});
