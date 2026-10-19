import { trimMarkdownLeadingPad } from "@/lib/trimMarkdownLeadingPad";
import { describe, expect, it } from "vitest";

describe("trimMarkdownLeadingPad", () => {
  it("strips 4-space pad that would become an indented code block", () => {
    expect(trimMarkdownLeadingPad("    工作区是空的，我会做成单页博客。")).toBe(
      "工作区是空的，我会做成单页博客。",
    );
  });

  it("leaves a fenced block intact", () => {
    const fenced = "```js\nconst x = 1\n```";
    expect(trimMarkdownLeadingPad(fenced)).toBe(fenced);
  });
});
