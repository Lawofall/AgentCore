import { clampPreview, PREVIEW_CHAR_CAP } from "@/lib/clampPreview";
import { describe, expect, it } from "vitest";

describe("clampPreview", () => {
  it("leaves short text alone", () => {
    expect(clampPreview("abc")).toEqual({ text: "abc", truncated: false });
  });

  it("cuts at the cap and marks truncated", () => {
    const raw = `HEAD${"x".repeat(PREVIEW_CHAR_CAP)}TAIL`;
    const { text, truncated } = clampPreview(raw);
    expect(truncated).toBe(true);
    expect(text.startsWith("HEAD")).toBe(true);
    expect(text.endsWith("…")).toBe(true);
    expect(text.includes("TAIL")).toBe(false);
    expect(text.length).toBe(PREVIEW_CHAR_CAP + 2);
  });
});
