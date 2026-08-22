import { normalizeMermaidSource } from "@/lib/mermaidNormalize";
import { describe, expect, it } from "vitest";

describe("normalizeMermaidSource", () => {
  it("fixes bare Chinese subgraph labels", () => {
    const input = "flowchart TD\nsubgraph 应用层\nA --> B\nend";
    const out = normalizeMermaidSource(input);
    expect(out).toContain('subgraph sg_0["应用层"]');
    expect(out).not.toContain("subgraph 应用层");
  });

  it("leaves already-bracketed subgraph labels unchanged", () => {
    const input = 'flowchart TD\nsubgraph app["应用层"]\nA --> B\nend';
    expect(normalizeMermaidSource(input)).toBe(input);
  });

  it("leaves Unicode-id subgraphs with bracket titles unchanged", () => {
    const input =
      'flowchart LR\nsubgraph 图["一次 delegate"]\nA["foo-bar"] --> B\nend';
    expect(normalizeMermaidSource(input)).toBe(input);
  });

  it("expands ampersand target edges", () => {
    const input = "flowchart TD\nL --> H & I & J & K";
    const out = normalizeMermaidSource(input);
    expect(out).toContain("L --> H");
    expect(out).toContain("L --> I");
    expect(out).toContain("L --> J");
    expect(out).toContain("L --> K");
    expect(out).not.toContain("&");
  });

  it("expands mixed ampersand source and target edges", () => {
    const input = "flowchart TD\nJ & K --> F & G";
    const out = normalizeMermaidSource(input);
    expect(out).toContain("J --> F");
    expect(out).toContain("J --> G");
    expect(out).toContain("K --> F");
    expect(out).toContain("K --> G");
    expect(out).not.toContain("&");
  });

  it("expands labeled ampersand target edges", () => {
    const input = "flowchart TD\nA -->|yes| B & C";
    const out = normalizeMermaidSource(input);
    expect(out).toContain("A -->|yes| B");
    expect(out).toContain("A -->|yes| C");
    expect(out).not.toContain("&");
  });

  it("does not modify comment lines", () => {
    const input = "%% A --> B & C\nflowchart TD";
    const out = normalizeMermaidSource(input);
    expect(out).toContain("%% A --> B & C");
  });

  it("converts full-width colon in sequence messages to ASCII", () => {
    const input = "sequenceDiagram\n用户->>前端：点击按钮";
    const out = normalizeMermaidSource(input);
    expect(out).toContain("用户->>前端:点击按钮");
    expect(out).not.toContain("：");
  });

  it("converts curly quotes to ASCII delimiters", () => {
    const input = "flowchart TD\nA[\u201C开始\u201D] --> B[\u2018结束\u2019]";
    const out = normalizeMermaidSource(input);
    expect(out).toContain('A["开始"]');
    expect(out).toContain("B['结束']");
    expect(out).not.toMatch(/[\u201C\u201D\u2018\u2019]/);
  });

  it("preserves full-width punctuation inside quoted labels", () => {
    const input = 'flowchart TD\nA["用户：管理员，超级"] --> B';
    const out = normalizeMermaidSource(input);
    expect(out).toContain('A["用户：管理员，超级"]');
  });

  it("normalizes full-width edge-label pipes and spaces", () => {
    const input = "flowchart\u3000TD\nA --\uFF5Cyes\uFF5C-> B";
    const out = normalizeMermaidSource(input);
    expect(out).toContain("flowchart TD");
    expect(out).toContain("A --|yes|-> B");
    expect(out).not.toMatch(/[\u3000\uFF5C]/);
  });
});
