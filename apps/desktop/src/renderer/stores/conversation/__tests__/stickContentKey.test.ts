import { stickContentKey } from "@/stores/conversation/selectors";
import { describe, expect, it } from "vitest";

describe("stickContentKey", () => {
  const base = {
    id: "m1",
    content: "清晰度是 1080p",
    reasoning: "想了一下",
    isStreaming: true,
  };

  it("is empty when there is no tail message", () => {
    expect(stickContentKey(null)).toBe("");
    expect(stickContentKey(undefined)).toBe("");
  });

  it("changes when streaming settles even if token lengths stay the same", () => {
    const live = stickContentKey(base);
    const settled = stickContentKey({ ...base, isStreaming: false });
    expect(live).not.toBe(settled);
    expect(live.endsWith("-1")).toBe(true);
    expect(settled.endsWith("-0")).toBe(true);
  });

  it("still changes when content or reasoning grows during a live turn", () => {
    const before = stickContentKey(base);
    expect(stickContentKey({ ...base, content: `${base.content}。` })).not.toBe(
      before,
    );
    expect(
      stickContentKey({ ...base, reasoning: `${base.reasoning}再想` }),
    ).not.toBe(before);
  });
});
