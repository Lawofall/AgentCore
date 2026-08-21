import { memoryUpdatedToastCopy } from "@/services/realtime";
import { describe, expect, it } from "vitest";

describe("memoryUpdatedToastCopy", () => {
  it("skips when the inline card is already on screen", () => {
    expect(memoryUpdatedToastCopy("semantic", true)).toBeNull();
    expect(memoryUpdatedToastCopy("quota", true)).toBeNull();
    expect(memoryUpdatedToastCopy("episodic", true)).toBeNull();
  });

  it("skips episodic session digests even when the user is away", () => {
    expect(memoryUpdatedToastCopy("episodic", false)).toBeNull();
  });

  it("heads-up semantic writes when away", () => {
    expect(memoryUpdatedToastCopy("semantic", false)).toBe(
      "AI 刚刚更新了你的记忆",
    );
  });

  it("heads-up quota refusals when away", () => {
    expect(memoryUpdatedToastCopy("quota", false)).toBe(
      "常驻条目已满，有内容没能记下",
    );
  });
});
