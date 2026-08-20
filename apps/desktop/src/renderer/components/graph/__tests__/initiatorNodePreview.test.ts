// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { initiatorNodePreview } from "../EndpointNode";

describe("initiatorNodePreview", () => {
  it("strips worker-count prefix so the preview does not fight the n/m denominator", () => {
    expect(initiatorNodePreview("1 个 worker：研究员")).toBe("研究员");
    expect(initiatorNodePreview("1 个 worker: 研究员")).toBe("研究员");
    expect(initiatorNodePreview("2 个 worker：研究员、撰写员")).toBe(
      "研究员、撰写员",
    );
  });

  it("hides a count-only summary", () => {
    expect(initiatorNodePreview("1 个 worker")).toBe("");
    expect(initiatorNodePreview("2 个 workers")).toBe("");
  });

  it("keeps ordinary task summaries", () => {
    expect(initiatorNodePreview("分析对比 React 和 Vue")).toBe(
      "分析对比 React 和 Vue",
    );
    expect(initiatorNodePreview("调研竞品")).toBe("调研竞品");
  });

  it("trims empty input", () => {
    expect(initiatorNodePreview("")).toBe("");
    expect(initiatorNodePreview("  ")).toBe("");
    expect(initiatorNodePreview(null)).toBe("");
    expect(initiatorNodePreview(undefined)).toBe("");
  });
});
