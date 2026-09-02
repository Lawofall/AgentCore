import type { ContextBlockWire } from "@/types/events";
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
      "前置结果 · 调研员",
      "前置结果 · 撰写员",
    ]);
  });

  it("keeps system as one 常驻指令 item with the full body", () => {
    const text = `你是 CEO。

<output_style>
- 不用 emoji
</output_style>

<tool_use>
并行调用独立工具。
</tool_use>`;
    const groups = buildReceivedContextCatalog(
      [block({ channel: "system", body: text, chars: text.length })],
      { includeSystem: true },
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("standing");
    expect(groups[0].items).toHaveLength(1);
    expect(groups[0].items[0].label).toBe("常驻指令");
    expect(groups[0].items[0].body).toBe(text);
    expect(groups[0].items[0].chars).toBe(text.length);
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
});
