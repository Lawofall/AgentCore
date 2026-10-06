import { findTexMathSpans, texDelimitersToDollars } from "@/lib/texDelimiters";
import { describe, expect, it } from "vitest";

describe("findTexMathSpans", () => {
  it("finds inline \\( \\)", () => {
    const spans = findTexMathSpans("see \\(a^2 + b^2\\) here");
    expect(spans).toHaveLength(1);
    expect(spans[0]).toMatchObject({ tex: "a^2 + b^2", display: false });
  });

  it("finds display \\[ \\] across lines", () => {
    const src = "intro\n\\[\nE = mc^2\n\\]\nend";
    const spans = findTexMathSpans(src);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.display).toBe(true);
    expect(spans[0]?.tex).toBe("\nE = mc^2\n");
  });

  it("finds one-line display", () => {
    const src = "\\[E = mc^2\\]";
    expect(findTexMathSpans(src)[0]).toMatchObject({
      tex: "E = mc^2",
      display: true,
    });
  });

  it("treats multiline \\( as display (remark-math $ cannot wrap)", () => {
    const src = "\\(\nx\n\\)";
    expect(findTexMathSpans(src)[0]?.display).toBe(true);
  });

  it("skips fenced code", () => {
    const src = "```\n\\(x\\)\n```\n\\(y\\)";
    const spans = findTexMathSpans(src);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.tex).toBe("y");
  });

  it("skips inline code", () => {
    const src = "use `\\(x\\)` then \\(y\\)";
    const spans = findTexMathSpans(src);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.tex).toBe("y");
  });

  it("ignores unclosed delimiters", () => {
    expect(findTexMathSpans("\\(no close")).toEqual([]);
    expect(findTexMathSpans("\\[no close")).toEqual([]);
  });

  it("does not treat \\[text](url) as math (no closing \\])", () => {
    expect(findTexMathSpans("\\[text](url)")).toEqual([]);
  });

  it("ignores escaped backslash before the opener", () => {
    expect(findTexMathSpans("\\\\(x\\)")).toEqual([]);
  });
});

describe("texDelimitersToDollars", () => {
  it("rewrites inline and display for remark-math", () => {
    const src = "see \\(a^2\\) and\n\\[\nE=mc^2\n\\]";
    expect(texDelimitersToDollars(src)).toBe("see $a^2$ and\n$$\nE=mc^2\n$$");
  });

  it("leaves dollar math and prose alone", () => {
    const src = "keep $a$ and $$b$$ and $100";
    expect(texDelimitersToDollars(src)).toBe(src);
  });

  it("is a no-op when there are no tex delimiters", () => {
    expect(texDelimitersToDollars("plain")).toBe("plain");
  });

  it("unwraps a nested inline delimiter inside display math", () => {
    const src = [
      "一个任务 \\(t\\) 是否派，形式化为：",
      "",
      "\\[",
      "\\forall i\\ \\operatorname{Unit}(i)，或整个 \\(t\\) 作为不可分单元；\\\\",
      "\\]",
    ].join("\n");
    const out = texDelimitersToDollars(src);
    expect(out).toContain("$t$");
    expect(out).not.toContain("\\(");
    expect(out).toContain("或整个 t 作为不可分单元");
  });

  it("unwraps same-type nesting down to the symbol", () => {
    expect(findTexMathSpans("\\[\\(\\(t\\)\\)\\]")[0]?.tex).toBe("t");
  });

  it("leaves an unclosed inner delimiter in the formula", () => {
    expect(findTexMathSpans("\\[a \\(b\\]")[0]?.tex).toBe("a \\(b");
  });
});
