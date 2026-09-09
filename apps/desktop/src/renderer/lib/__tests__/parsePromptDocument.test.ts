import {
  hasTaggedSections,
  parsePromptDocument,
} from "@/lib/parsePromptDocument";
import { describe, expect, it } from "vitest";

describe("parsePromptDocument", () => {
  it("returns a single untagged section when no XML tags are present", () => {
    const sections = parsePromptDocument("plain text\nwith lines");
    expect(sections).toEqual([
      { tag: null, title: "", body: "plain text\nwith lines" },
    ]);
    expect(hasTaggedSections(sections)).toBe(false);
  });

  it("splits preamble and tagged sections", () => {
    const text = `你是 AgentCore 的一员。

<output_style>
- 不用 emoji
</output_style>

<tool_use>
并行调用独立工具。
</tool_use>`;

    const sections = parsePromptDocument(text);
    expect(sections).toHaveLength(3);
    expect(sections[0]).toMatchObject({
      tag: null,
      title: "概述",
      body: "你是 AgentCore 的一员。",
    });
    expect(sections[1]).toMatchObject({
      tag: "output_style",
      title: "输出风格",
      body: "- 不用 emoji",
    });
    expect(sections[2]).toMatchObject({
      tag: "tool_use",
      title: "工具使用",
    });
    expect(hasTaggedSections(sections)).toBe(true);
  });

  it("handles Chinese tag names", () => {
    const text = `<能力目录>
- staffing：进阶用法
</能力目录>`;

    const sections = parsePromptDocument(text);
    expect(sections).toHaveLength(1);
    expect(sections[0]).toMatchObject({
      tag: "能力目录",
      title: "能力目录",
      body: "- staffing：进阶用法",
    });
  });

  it("returns empty array for blank input", () => {
    expect(parsePromptDocument("   ")).toEqual([]);
  });
});
