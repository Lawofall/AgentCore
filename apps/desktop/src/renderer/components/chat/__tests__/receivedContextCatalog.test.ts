import type { ContextBlockWire, ProcessStep } from "@/types/events";
import { describe, expect, it } from "vitest";
import {
  buildReceivedContextCatalog,
  defaultCatalogItemId,
  flattenCatalog,
} from "../receivedContextCatalog";

function block(
  overrides: Partial<ContextBlockWire> & Pick<ContextBlockWire, "channel">,
): ContextBlockWire {
  return {
    heading: "heading",
    body: "body",
    chars: 4,
    truncated: false,
    files: [],
    source_role: "",
    source_run_id: "",
    fidelity: "",
    ...overrides,
  };
}

describe("buildReceivedContextCatalog", () => {
  it("omits empty groups and keeps wire order inside a group", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "request", body: "目标" }),
        block({ channel: "team_position", body: "位置" }),
        block({ channel: "dependency", body: "上游", source_role: "调研员" }),
        block({ channel: "task", body: "任务" }),
      ],
      { includeSystem: true },
    );
    expect(groups.map((g) => g.id)).toEqual([
      "turn",
      "material",
      "environment",
    ]);
    expect(groups[0].items.map((i) => i.channel)).toEqual(["request", "task"]);
    expect(groups.find((g) => g.id === "history")).toBeUndefined();
    expect(groups.find((g) => g.id === "standing")).toBeUndefined();
  });

  it("buckets 本回合工具 with request under 本回合", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "system", body: "核" }),
        block({ channel: "tools", body: "**web_search**" }),
        block({ channel: "request", body: "目标" }),
      ],
      { includeSystem: true },
    );
    const turn = groups.find((g) => g.id === "turn");
    expect(turn?.items.map((i) => i.channel)).toEqual(["tools", "request"]);
    expect(turn?.items[0]?.label).toBe("本回合工具");
  });

  it("labels material rows with source role", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({
          channel: "dependency",
          source_role: "调研员",
          body: "a",
        }),
        block({
          channel: "dependency",
          source_role: "撰写员",
          body: "b",
        }),
      ],
      { includeSystem: true },
    );
    expect(groups[0].items.map((i) => i.label)).toEqual([
      "前置 · 调研员",
      "前置 · 撰写员",
    ]);
  });

  it("lifts 设定 / 按需目录 / 工作区 and keeps factory as one row", () => {
    const text = `互不依赖。

<输出>
直接给结论。
</输出>

<设定>
我的规则。
</设定>

<按需目录>
consult(name) 拉全文。
</按需目录>

<工作区>
桌面已连接。
</工作区>`;
    const groups = buildReceivedContextCatalog(
      [block({ channel: "system", body: text, chars: text.length })],
      { includeSystem: true },
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("standing");
    const labels = groups[0].items.map((i) => i.label);
    expect(labels).toEqual(["设定", "按需目录", "工作区", "出厂指令"]);
    const setting = groups[0].items[0];
    expect(setting?.tag).toBe("设定");
    expect(setting?.absent).toBe(false);
    expect(setting?.body).toContain("我的规则。");
    expect(groups[0].items.some((i) => i.absent)).toBe(false);
    const factory = groups[0].items.find((i) => i.label === "出厂指令");
    expect(factory?.body).toContain("互不依赖。");
    expect(factory?.body).toContain("直接给结论。");
    expect(factory?.body).not.toContain("我的规则。");
  });

  it("lifts path-rule index and attachment full text beside 设定", () => {
    const text = `适合可视化的内容，优先采用可视化呈现。

<设定>
常驻全文。
</设定>

<路径约定>
碰到匹配路径就遵守这一句。
- **/*.tsx：布局
</路径约定>

<路径约定全文>
### layout
页面留白。
</路径约定全文>

<按需目录>
consult(name) 拉全文。
</按需目录>`;
    const groups = buildReceivedContextCatalog(
      [block({ channel: "system", body: text, chars: text.length })],
      { includeSystem: true },
    );
    const labels = groups[0].items.map((i) => i.label);
    expect(labels).toEqual([
      "设定",
      "路径约定",
      "路径约定全文",
      "按需目录",
      "出厂指令",
    ]);
    const index = groups[0].items.find((i) => i.tag === "路径约定");
    const full = groups[0].items.find((i) => i.tag === "路径约定全文");
    expect(index?.body).toContain("碰到匹配路径就遵守这一句。");
    expect(full?.body).toContain("页面留白。");
    const factory = groups[0].items.find((i) => i.label === "出厂指令");
    expect(factory?.body).toContain("适合可视化的内容");
    expect(factory?.body).not.toContain("碰到匹配路径");
    expect(factory?.body).not.toContain("页面留白");
  });

  it("marks missing 设定 as an absent row instead of backfilling", () => {
    const text = `你是 CEO。

<output_style>
- 不用 emoji
</output_style>`;
    const groups = buildReceivedContextCatalog(
      [block({ channel: "system", body: text, chars: text.length })],
      { includeSystem: true },
    );
    const items = flattenCatalog(groups);
    const setting = items.find((i) => i.tag === "设定");
    expect(setting?.absent).toBe(true);
    expect(setting?.chars).toBe(0);
    expect(items.find((i) => i.label === "出厂指令")?.body).toContain(
      "你是 CEO。",
    );
  });

  it("hides the standing group when includeSystem is false", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "system", body: "<role>CEO</role>" }),
        block({ channel: "request", body: "目标" }),
      ],
      { includeSystem: false },
    );
    expect(groups.map((g) => g.id)).toEqual(["turn"]);
    expect(flattenCatalog(groups).some((i) => i.channel === "system")).toBe(
      false,
    );
  });

  it("indexes consult receipts from process without inventing a system slice", () => {
    const process: ProcessStep[] = [
      {
        kind: "tool",
        id: "c1",
        tool_name: "consult",
        arguments: { name: "写作风格" },
        result: "按需规则全文。",
        status: "success",
        display: { name: "写作风格", origin: "user" },
      },
    ];
    const groups = buildReceivedContextCatalog(
      [block({ channel: "request", body: "目标" })],
      { includeSystem: true, process },
    );
    expect(groups.map((g) => g.id)).toEqual(["turn", "later"]);
    const later = groups.find((g) => g.id === "later")?.items[0];
    expect(later?.channel).toBe("consult_receipt");
    expect(later?.label).toBe("查阅 · 写作风格");
    expect(later?.body).toBe("按需规则全文。");
  });

  it("buckets unknown channels into 其他", () => {
    const groups = buildReceivedContextCatalog(
      [block({ channel: "not_a_real_channel" as ContextBlockWire["channel"] })],
      { includeSystem: true },
    );
    expect(groups).toEqual([
      expect.objectContaining({
        id: "other",
        label: "其他",
      }),
    ]);
  });
});

describe("defaultCatalogItemId", () => {
  it("selects the request row even when it is not first", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "history", body: "旧" }),
        block({ channel: "request", body: "目标" }),
        block({ channel: "task", body: "活" }),
      ],
      { includeSystem: true },
    );
    const id = defaultCatalogItemId(groups);
    const item = flattenCatalog(groups).find((i) => i.id === id);
    expect(item?.channel).toBe("request");
  });

  it("selects injected 设定 over 本回合工具 and 原始请求", () => {
    const text = `<设定>
我的规则。
</设定>`;
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "system", body: text, chars: text.length }),
        block({ channel: "tools", body: "**web_search**" }),
        block({ channel: "request", body: "目标" }),
      ],
      { includeSystem: true },
    );
    expect(groups.map((g) => g.id)).toEqual(["standing", "turn"]);
    const id = defaultCatalogItemId(groups);
    const item = flattenCatalog(groups).find((i) => i.id === id);
    expect(item?.tag).toBe("设定");
    expect(item?.absent).toBe(false);
  });

  it("falls back to 原始请求 when 设定 was not injected", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "system", body: "核" }),
        block({ channel: "tools", body: "**web_search**" }),
        block({ channel: "request", body: "目标" }),
      ],
      { includeSystem: true },
    );
    const id = defaultCatalogItemId(groups);
    const item = flattenCatalog(groups).find((i) => i.id === id);
    expect(item?.channel).toBe("request");
    expect(flattenCatalog(groups).find((i) => i.tag === "设定")?.absent).toBe(
      true,
    );
  });

  it("falls back to the first TOC row when there is no request", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "history", body: "旧" }),
        block({ channel: "workspace", body: "文件" }),
      ],
      { includeSystem: true },
    );
    const id = defaultCatalogItemId(groups);
    const item = flattenCatalog(groups).find((i) => i.id === id);
    expect(item?.channel).toBe("history");
  });

  it("preferMaterial selects the first 材料 row over request", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "request", body: "目标" }),
        block({
          channel: "dependency",
          source_role: "调研员",
          body: "上游",
        }),
      ],
      { includeSystem: true },
    );
    const id = defaultCatalogItemId(groups, { preferMaterial: true });
    const item = flattenCatalog(groups).find((i) => i.id === id);
    expect(item?.channel).toBe("dependency");
  });

  it("preferMaterial still wins over 本回合工具", () => {
    const groups = buildReceivedContextCatalog(
      [
        block({ channel: "tools", body: "**web_search**" }),
        block({
          channel: "dependency",
          source_role: "调研员",
          body: "上游",
        }),
      ],
      { includeSystem: true },
    );
    const id = defaultCatalogItemId(groups, { preferMaterial: true });
    const item = flattenCatalog(groups).find((i) => i.id === id);
    expect(item?.channel).toBe("dependency");
  });
});
