import { trimMarkdownLeadingPad } from "@/lib/trimMarkdownLeadingPad";
import { describe, expect, it } from "vitest";

describe("trimMarkdownLeadingPad", () => {
  it("strips 4-space pad that would become an indented code block", () => {
    expect(trimMarkdownLeadingPad("    工作区是空的，我会做成单页博客。")).toBe(
      "工作区是空的，我会做成单页博客。",
    );
  });

  it("strips a 6-space pad and leading newlines", () => {
    expect(trimMarkdownLeadingPad("\n      首页骨架已完成。")).toBe(
      "首页骨架已完成。",
    );
  });

  it("leaves a fenced block intact", () => {
    const fenced = "```js\nconst x = 1\n```";
    expect(trimMarkdownLeadingPad(fenced)).toBe(fenced);
  });

  it("keeps trailing spaces while streaming", () => {
    expect(trimMarkdownLeadingPad("  正在创建  ")).toBe("正在创建  ");
  });

  it("turns whitespace-only chunks into empty", () => {
    expect(trimMarkdownLeadingPad("   \n")).toBe("");
  });
});
