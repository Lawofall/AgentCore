import { turnDetailPath } from "@/stores/ui";
import { describe, expect, it } from "vitest";

/** Mirrors InlineTeamGraph openInCanvas view arg for debate CTA. */
function canvasOpenView(debate: boolean): "debate" | undefined {
  return debate ? "debate" : undefined;
}

describe("InlineTeamGraph openInCanvas view (双入口契约 B)", () => {
  const cid = "c1";
  const mid = "m1";

  it("passes view=debate for 打开辩论室", () => {
    expect(turnDetailPath(cid, mid, canvasOpenView(true))).toBe(
      `/conversations/${cid}/turn/${mid}?view=debate`,
    );
  });

  it("omits view for non-debate 在画布打开", () => {
    expect(turnDetailPath(cid, mid, canvasOpenView(false))).toBe(
      `/conversations/${cid}/turn/${mid}`,
    );
  });
});
