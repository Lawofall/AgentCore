import { describe, expect, it } from "vitest";
import { introChapter } from "../content/intro";
import {
  buildChapterSearchEntries,
  buildContentSearchEntries,
  extractBlockText,
  matchSnippet,
} from "../searchIndex";
import type { ManualBlock } from "../types";

describe("searchIndex", () => {
  it("extracts text from all block types", () => {
    const blocks: ManualBlock[] = [
      { type: "lead", text: "导语" },
      {
        type: "paragraph",
        text: [{ text: "粗", strong: true }, "段"],
        emphasis: true,
      },
      { type: "callout", variant: "tip", text: "提示" },
      {
        type: "cards",
        items: [{ title: "卡", desc: "述" }],
      },
      {
        type: "bullets",
        items: [{ title: "点", desc: "明" }],
      },
      {
        type: "steps",
        items: [
          {
            title: "步",
            desc: [
              "去",
              { text: "设置", link: { kind: "go", to: "/more/model" } },
            ],
          },
        ],
      },
      {
        type: "doDont",
        good: { label: "好", items: ["G"] },
        bad: { items: ["B"] },
      },
      {
        type: "faq",
        items: [
          {
            q: "问什么？",
            a: [
              { type: "text", text: "答" },
              {
                type: "boundaryTable",
                rows: [{ can: "C", approve: "A", wont: "W" }],
              },
            ],
          },
        ],
      },
      {
        type: "boundaryTable",
        rows: [{ can: "c", approve: "a", wont: "w" }],
      },
      {
        type: "settingsRows",
        rows: [{ label: "模型", desc: "Key", to: "/more/model" }],
      },
      { type: "embed", key: "HeroGraph" },
    ];

    const joined = blocks.map(extractBlockText).join(" ");
    expect(joined).toContain("导语");
    expect(joined).toContain("粗");
    expect(joined).toContain("提示");
    expect(joined).toContain("卡");
    expect(joined).toContain("点");
    expect(joined).toContain("设置");
    expect(joined).toContain("好");
    expect(joined).toContain("问什么？");
    expect(joined).toContain("C");
    expect(joined).toContain("模型");
    // embed 不进索引
    expect(joined).not.toContain("HeroGraph");
  });

  it("builds full-text entries from intro content", () => {
    const entries = buildChapterSearchEntries(introChapter);
    expect(entries).toHaveLength(3);

    const quick = entries.find((e) => e.itemId === "quickstart");
    expect(quick?.label).toBe("5 分钟上手");
    expect(quick?.to).toBe("/toolbox/manual/intro?s=quickstart");
    expect(quick?.haystack).toContain("5 分钟");
    // BYOK 仍可被搜到（可选升级），但第一步是「说目标」——开箱即用，不先支去外部站点。
    expect(quick?.haystack).toContain("api key");
    const quickBody = quick?.body ?? "";
    expect(quickBody).toContain("说目标");
    expect(quickBody.indexOf("说目标")).toBeLessThan(quickBody.indexOf("BYOK"));

    const what = entries.find((e) => e.itemId === "what");
    expect(what?.haystack).toContain("协作，是更高级的智能");
    expect(what?.haystack).not.toContain("chatgpt");
  });

  it("matchSnippet centers around the query", () => {
    const body = "前面填充文字。填 Key 去模型配置。后面还有说明。";
    expect(matchSnippet(body, "填 key")).toMatch(/填 Key/);
  });

  it("indexes workflow / automation sections so search can reach them", () => {
    const entries = buildContentSearchEntries();

    const workflow = entries.find((e) => e.id === "collaboration-workflow");
    expect(workflow?.label).toBe("工作流");
    expect(workflow?.to).toBe("/toolbox/manual/collaboration?s=workflow");
    expect(workflow?.haystack).toContain("官方模板");
    expect(workflow?.haystack).toContain("等人关卡");

    const automation = entries.find((e) => e.id === "collaboration-automation");
    expect(automation?.label).toBe("自动化");
    expect(automation?.to).toBe("/toolbox/manual/collaboration?s=automation");
    expect(automation?.haystack).toContain("webhook");
    expect(automation?.haystack).toContain("系统任务");
    expect(automation?.haystack).toContain("收件箱");
  });

  it("content search entries cover all four chapters", () => {
    const entries = buildContentSearchEntries();
    for (const chapter of [
      "intro",
      "collaboration",
      "mechanism",
      "reference",
    ]) {
      expect(
        entries.some((e) => e.id.startsWith(`${chapter}-`)),
        `missing entries for chapter ${chapter}`,
      ).toBe(true);
    }
  });
});
