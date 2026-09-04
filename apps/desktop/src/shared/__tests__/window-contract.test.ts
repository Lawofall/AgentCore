import { toggleWindowFramePreset } from "@shared/window-contract";
import { describe, expect, it } from "vitest";

describe("toggleWindowFramePreset", () => {
  it("locks the clicked ratio from free", () => {
    expect(toggleWindowFramePreset("free", "16:9-1080")).toBe("16:9-1080");
    expect(toggleWindowFramePreset("free", "4:3-uxga")).toBe("4:3-uxga");
  });

  it("unlocks when the active ratio is clicked again", () => {
    expect(toggleWindowFramePreset("16:9-1080", "16:9-1080")).toBe("free");
    expect(toggleWindowFramePreset("4:3-uxga", "4:3-uxga")).toBe("free");
  });

  it("switches to the other ratio without unlocking", () => {
    expect(toggleWindowFramePreset("16:9-1080", "4:3-uxga")).toBe("4:3-uxga");
    expect(toggleWindowFramePreset("4:3-uxga", "16:9-1080")).toBe("16:9-1080");
  });
});
